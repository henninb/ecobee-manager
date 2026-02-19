#!/usr/bin/env python3
"""
Ecobee CLI - interact with your thermostat from the command line
Reads JWT from ecobee_jwt.json

Usage:
    python ecobee_cli.py status          # Show thermostat info
    python ecobee_cli.py get             # Get current temperature setting
    python ecobee_cli.py set <temp>      # Set temperature (°F)
    python ecobee_cli.py sensors         # List all sensors with temp and occupancy
    python ecobee_cli.py lean <temp>     # Pick sensors that pull the average toward <temp>
    python ecobee_cli.py schedule        # Show current Ecobee program schedule
    python ecobee_cli.py schedule-night  # Set Ecobee program: alternate sleep/smart1 every hour all day
"""

import sys
import json
import os
from datetime import datetime, timezone

from temperature_controller import TemperatureController

JWT_FILE = "ecobee_jwt.json"


def load_token() -> dict:
    if not os.path.exists(JWT_FILE):
        print(f"Error: No token file found at {JWT_FILE}")
        print("Set your JWT in that file first.")
        sys.exit(1)

    with open(JWT_FILE) as f:
        config = json.load(f)

    token = config.get("jwt_token")
    if not token:
        print("Error: No token found in config file")
        sys.exit(1)

    expires_at = config.get("token_expires_at")
    if expires_at:
        expiry = datetime.fromisoformat(expires_at)
        if datetime.now(timezone.utc) > expiry:
            print(f"Warning: Token may be expired at {expiry}.")

    return {
        "token": token,
        "base_url": config.get("api_base_url"),
    }


def cmd_status(controller: TemperatureController):
    info = controller.get_thermostat_info()
    if not info:
        print("Error: Could not retrieve thermostat info")
        sys.exit(1)

    print(f"Name:          {info['name']}")
    print(f"Model:         {info['model']}")
    print(f"Actual temp:   {info['actual_temperature']}°F")
    print(f"Desired heat:  {info['desired_heat']}°F")
    print(f"Desired cool:  {info['desired_cool']}°F")
    print(f"HVAC mode:     {info['hvac_mode']}")
    print(f"Active hold:   {info['has_active_hold']}")


def cmd_get(controller: TemperatureController):
    temp = controller.get_current_temperature_setting()
    if temp is None:
        print("Error: Could not retrieve current temperature setting")
        sys.exit(1)
    print(f"Current setting: {temp}°F")


def cmd_sensors(controller: TemperatureController):
    sensors = controller.get_sensors()
    if sensors is None:
        print("Error: Could not retrieve sensors")
        sys.exit(1)
    if not sensors:
        print("No sensors found")
        return

    for s in sensors:
        temp = f"{s['temperature']}°F" if s['temperature'] is not None else "n/a"
        occupancy = s['occupancy'] if s['occupancy'] is not None else "n/a"
        in_use = "yes" if s['in_use'] else "no"
        print(f"{s['name']:<20} temp={temp:<10} occupancy={occupancy:<6} in_use={in_use}  type={s['type']}")


def cmd_lean(controller: TemperatureController, args):
    if not args:
        print("Usage: ecobee_cli.py lean <temperature>")
        sys.exit(1)

    try:
        target = float(args[0])
    except ValueError:
        print(f"Error: Invalid temperature '{args[0]}'")
        sys.exit(1)

    info = controller.get_climate_sensor_info()
    if not info:
        print("Error: Could not retrieve climate/sensor info")
        sys.exit(1)

    climate_ref = info['current_climate_ref']
    raw_sensors = info['raw_sensors']

    print(f"Active climate: {climate_ref}")
    print(f"Target temperature: {target}°F")
    print()

    # Show all sensor readings
    for s in raw_sensors:
        caps = {c['type']: c['value'] for c in s.get('capability', [])}
        temp_raw = caps.get('temperature')
        temp_f = f"{int(temp_raw) / 10}°F" if temp_raw and temp_raw != 'unknown' else "n/a"
        print(f"  {s['name']:<20} {temp_f}")

    print()

    climate_refs = [c.get('climateRef') for c in info['climates']]
    print(f"Climates in program: {', '.join(climate_refs)}")
    print()

    selected = controller.select_sensors_toward_target(raw_sensors, target, info.get('climate_sensor_map'))
    selected_names = [s['name'] for s in selected]
    print(f"Selected sensors (lean toward {target}°F): {', '.join(selected_names)}")

    dry_run = '--dry-run' in args
    if dry_run:
        body = controller.build_climate_update_body(info['thermostat_id'], climate_ref, info['climates'], selected, info['schedule'])
        print("=== DRY RUN: POST body ===")
        print(json.dumps(body, indent=2))
        return

    if controller.update_climate_sensors(info['thermostat_id'], climate_ref, info['climates'], selected, info['schedule']):
        print(f"Done: Climate '{climate_ref}' updated to use {len(selected)} sensor(s)")
    else:
        print("Error: Failed to update climate sensors")
        sys.exit(1)


def cmd_dump_program(controller: TemperatureController):
    """Dump raw program JSON for debugging"""
    info = controller.get_climate_sensor_info()
    if not info:
        print("Error: Could not retrieve program info")
        sys.exit(1)

    print("=== CLIMATES ===")
    print(json.dumps(info['climates'], indent=2))
    print()

    schedule = info.get('schedule', [])
    print(f"=== SCHEDULE ({len(schedule)} days, {len(schedule[0]) if schedule else 0} slots/day) ===")

    unique_refs = sorted({slot for day in schedule for slot in day})
    print(f"Unique climate refs in schedule: {unique_refs}")

    climate_refs_in_program = {c.get('climateRef') for c in info['climates']}
    missing = [r for r in unique_refs if r not in climate_refs_in_program]
    if missing:
        print(f"WARNING: Schedule references climates NOT in program climates: {missing}")
    else:
        print("OK: All schedule refs are present in climates list")

    print()
    print("=== RAW SENSORS (IDs) ===")
    for s in info['raw_sensors']:
        caps = {c['type']: c['value'] for c in s.get('capability', [])}
        temp_raw = caps.get('temperature')
        temp_f = f"{int(temp_raw) / 10}°F" if temp_raw and temp_raw != 'unknown' else "n/a"
        print(f"  id={s.get('id'):<20} name={s.get('name'):<25} temp={temp_f}")


def _fmt_hour(hour: int) -> str:
    """Convert 24-hour integer to 12-hour label like '11pm', '12am', '1am'."""
    if hour == 0:
        return "12am"
    elif hour < 12:
        return f"{hour}am"
    elif hour == 12:
        return "12pm"
    else:
        return f"{hour - 12}pm"


def print_program_schedule(info: dict):
    """Print only the night-window hours (11pm–6am) of the Ecobee program schedule."""
    DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

    climate_map = {
        c.get('climateRef'): {
            'name': c.get('name', c.get('climateRef')),
            'heat': c.get('heatTemp', 0) / 10,
            'cool': c.get('coolTemp', 0) / 10,
        }
        for c in info.get('climates', [])
    }

    ALL_HOURS = list(range(0, 24))

    schedule = info.get('schedule', [])
    print()
    print(f"{'Day':<12} {'Time':<14} {'Climate':<10} {'Heat':>6}  {'Cool':>6}")
    print("-" * 52)

    for day_idx, day_slots in enumerate(schedule):
        day_name = DAY_NAMES[day_idx] if day_idx < len(DAY_NAMES) else f"Day{day_idx}"
        first_row = True
        for hour in ALL_HOURS:
            slot = hour * 2
            ref = day_slots[slot]
            start_label = _fmt_hour(hour)
            end_label   = _fmt_hour((hour + 1) % 24)
            time_range  = f"{start_label}-{end_label}"
            climate = climate_map.get(ref, {})
            name = climate.get('name', ref)
            heat = f"{climate['heat']:.0f}°F" if 'heat' in climate else "n/a"
            cool = f"{climate['cool']:.0f}°F" if 'cool' in climate else "n/a"
            label = day_name if first_row else ""
            first_row = False
            print(f"{label:<12} {time_range:<14} {name:<10} {heat:>6}  {cool:>6}")


def cmd_schedule_night(controller: TemperatureController, args):
    """Set the Ecobee program to alternate sleep/smart1 every hour across all 24 hours every day."""
    dry_run = '--dry-run' in args

    result = controller.update_night_schedule(
        temp=67, climate_ref="sleep", alt_climate_ref="smart1",
        start_hour=0, end_hour=0, dry_run=dry_run
    )

    if dry_run:
        print("=== DRY RUN: POST body ===")
        print(json.dumps(result, indent=2))
        return

    if not result:
        print("Error: Failed to update night schedule")
        sys.exit(1)

    print("Done: Ecobee program updated.")

    info = controller.get_climate_sensor_info()
    if info:
        print_program_schedule(info)
    else:
        print("Warning: Could not fetch updated schedule for display.")


def cmd_schedule(controller: TemperatureController):
    """Show the current Ecobee program schedule."""
    info = controller.get_climate_sensor_info()
    if not info:
        print("Error: Could not retrieve program schedule")
        sys.exit(1)
    print_program_schedule(info)


def cmd_set(controller: TemperatureController, args):
    if not args:
        print("Usage: ecobee_cli.py set <temperature>")
        sys.exit(1)

    try:
        target = int(args[0])
    except ValueError:
        print(f"Error: Invalid temperature '{args[0]}' — must be an integer")
        sys.exit(1)

    if target < 40 or target > 90:
        print(f"Error: Temperature {target}°F is out of safe range (40–90°F)")
        sys.exit(1)

    print(f"Setting temperature to {target}°F...")
    if controller.set_temperature(target):
        print(f"Done: Temperature set to {target}°F")
    else:
        print("Error: Failed to set temperature")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    args = sys.argv[2:]

    ctx = load_token()
    controller = TemperatureController(ctx["token"], base_url=ctx["base_url"])

    if command == "schedule":
        cmd_schedule(controller)
    elif command == "schedule-night":
        cmd_schedule_night(controller, args)
    elif command == "status":
        cmd_status(controller)
    elif command == "get":
        cmd_get(controller)
    elif command == "set":
        cmd_set(controller, args)
    elif command == "sensors":
        cmd_sensors(controller)
    elif command == "lean":
        cmd_lean(controller, args)
    elif command == "dump-program":
        cmd_dump_program(controller)
    else:
        print(f"Unknown command: {command}")
        print("Commands: status, get, set <temp>, sensors, lean <temp>, schedule, schedule-night")
        sys.exit(1)


if __name__ == "__main__":
    main()
