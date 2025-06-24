#!/usr/bin/env python3

## TODO:
#   - Add support for Ace Pro being on it's own UPS
#   - Combine get_ and set_ functions for nozzle/bed/print status
#   - Dynamically set Klippy and Moonraker connection info
#   - Add support for future config editing on touch UI
#   - Show and select from list of multiple UPS's in touch UI
#   - Add logging
#   - React to UPS states
#   - Move config files to config directory

import configparser
import json
import random
import requests
import signal
import socket
import sys
import time

def handler(sig, frame):
    sys.exit(0)

def read_config_file(filename="nut-client-config.ini"):
    config = configparser.ConfigParser()
    try:
        with open(filename, "r") as f:
            config.read_file(f)
    except Exception as e:
        raise Exception(f"Error reading config file {filename}: {e}")
    else:
        global ups_name, nut_address, nut_port, nut_user, nut_password, is_on_printer
        section = config["nut"]
        ups_name = section.get('ups_name')  # Need clean error for when configured UPS does not exist
        nut_address = section.get('nut_address') or "localhost"
        nut_port = int(section.get('nut_port') or 3493)
        nut_user = section.get('nut_user')
        nut_password = section.get('nut_password')
        is_on_printer = section.get('is_on_printer', 'false').lower() in ('true', '1', 'yes') or False  # For testing outside of printer
        return True

def connect(sock, nut_address, nut_port=3493):
    try:
        sock.settimeout(10)
        sock.connect((nut_address, nut_port))
    except Exception as e:
        sock.close()
        raise Exception(f"Could not connect to {nut_address}:{nut_port} - {e}")
    else:
        return True

def login(sock, nut_user=None, nut_password=None, timeout=5):
    sock.settimeout(timeout)
    try: 
        if nut_user:
            sock.sendall(f"USERNAME {nut_user}\n".encode("utf-8"))
            resp = recv_line(sock)
            if resp != b"OK":
                text = resp.decode("utf-8", errors="replace")
                raise ValueError(f"Username not accepted, server replied: {text!r}")
            print(f"Username accepted")
        if nut_password:
            sock.sendall(f"PASSWORD {nut_password}\n".encode("utf-8"))
            resp = recv_line(sock)
            if resp != b"OK":
                text = resp.decode("utf-8", errors="replace")
                raise ValueError(f"Password not accepted, server replied: {text!r}")
            print(f"Password accepted")
    except socket.error as e:
        raise ConnectionError(f"Failed to send username: {e}") from e
    except socket.timeout as e:
        raise TimeoutError(f"No response for username within {timeout}s") from e
    except Exception as e:
        raise (f"Could not log in to user {user} - {e}")
    else:
        return True

def recv_line(sock, bufsize=256, eol=b"\n"): 
    data = bytearray()
    try:
        while True:
            chunk = sock.recv(bufsize)
            if not chunk:
                break
            data.extend(chunk)
            if eol in chunk:
                break
        line, *rest = data.split(eol, 1)
    except Exception as e:
        raise (f"Could not read data from UPS - {e}")
    else:
        return bytes(line)

def auto_select_ups(sock, bufsize=256, eol=b"\n"):
    try:
        sock.sendall(b"LIST UPS\n")
        data = bytearray()
        while True:
            chunk = sock.recv(bufsize)
            if not chunk:
                raise Exception("No data received after LIST UPS")
            data.extend(chunk)
            if b"BEGIN LIST UPS\n" in data:
                _, rest = data.split(b"BEGIN LIST UPS\n", 1)
                data = bytearray(rest)
                break
        while True:
            if eol in data:
                line, rest = data.split(eol, 1)
                data = bytearray(rest)
            else:
                chunk = sock.recv(bufsize)
                if not chunk:
                    raise Exception("Connection closed while reading UPS list")
                data.extend(chunk)
                continue
            text = line.decode("utf-8", errors="replace").strip()
            if text == "END LIST UPS":
                return "No UPS found"
            if text.startswith("UPS "):
                ups_name = text.split()[1]
                return ups_name
    except Exception as e:
        raise Exception(f"Unable to auto-select UPS - {e}")

def read_ups_vars(sock, ups_name, ups_vars, bufsize=256, eol=b"\n"):
    ups_vars.clear()
    try:
        sock.sendall(f"LIST VAR {ups_name}\n".encode("utf-8"))
        data = bytearray()
        start_marker = f"BEGIN LIST VAR {ups_name}\n".encode("utf-8")
        end_marker_text = f"END LIST VAR {ups_name}"
        while True:
            chunk = sock.recv(bufsize)
            if not chunk:
                raise Exception("No data received after LIST VAR")
            data.extend(chunk)
            if start_marker in data:
                data = bytearray(data.split(start_marker, 1)[1])
                break
        while True:
            if eol in data:
                line, data = data.split(eol, 1)
            else:
                chunk = sock.recv(bufsize)
                if not chunk:
                    raise Exception("No data received after LIST VAR")
                data.extend(chunk)
                continue
            text = line.decode("utf-8", errors="replace").strip()
            if text == end_marker_text:
                return
            if text.startswith("VAR "):
                parts = text.split()
                if len(parts) >= 4:
                    ups_vars.append(parts[2:4])
    except Exception as e:
        raise Exception(f"Unable to read vars from UPS - {e}")

def list_ups_vars(ups_name, ups_vars):
    print(f"UPS: {ups_name}")
    if not ups_vars:
        print("No variables found")
        return
    for var in ups_vars:
        print(f"{var[0]}: {var[1]}")

def read_ups_var(sock, ups_name, var_name, bufsize=256, eol=b"\n"):
    try:
        sock.sendall(f"GET VAR {ups_name} {var_name}\n".encode("utf-8"))
        data = bytearray()
        while True:
            chunk = sock.recv(bufsize)
            if not chunk:
                raise Exception("No data received from after GET VAR")
            data.extend(chunk)
            if eol in chunk:
                break
        line, *_ = data.split(eol, 1)
        text = line.decode("utf-8", errors="replace").strip()
        # expected format: GET VAR <ups_name> <var_name> "<value>"
        parts = text.split()
        if len(parts) < 4:
            raise Exception(f"Unexpected response: {text}")
        return parts[3].strip('"')
    except Exception as e:
        raise Exception(f"Unable to read var {var_name} from UPS - {e}")

def klippy_command(payload, socket_path="/tmp/unix_uds1", timeout=5):
    msg = json.dumps(payload).encode('utf-8') + b'\x03'
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(socket_path)
        sock.sendall(msg)
        data = bytearray()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if b'\x03' in chunk:
                break
        sock.shutdown(socket.SHUT_WR)
        sock.shutdown(socket.SHUT_RD)
    except Exception as e:
        print(f"Socket error: {e}")
        return False
    finally:
        sock.close()

    raw = data.rstrip(b'\x03')
    if not raw:
        print("No data returned from Klipper socket.")
        return False
    try:
        return json.loads(raw.decode('utf-8', errors='replace'))
    except json.JSONDecodeError as e:
        print("Failed to decode JSON:", e)
        print("Raw response:", raw)
        return False

def get_ace_pro_ids(socket_path="/tmp/unix_uds1"):
    payload = {
        "method": "objects/query",
        "params": {"objects": {"filament_hub": None}},
        "id": random.randint(0, 32767)
    }
    resp = klippy_command(payload, socket_path)
    if not resp:
        print("No response from klippy_command()")
        return []
    try:
        hubs = resp["result"]["status"]["filament_hub"]["filament_hubs"]
        return [hub["id"] for hub in hubs]
    except (KeyError, TypeError) as e:
        print("Error parsing filament_hub response:", e)
        return []

def get_ace_pro_status(ace_id, socket_path="/tmp/unix_uds1"):
    payload = {
        "method": "objects/query",
        "params": {"objects": {"filament_hub": None}},
        "id": random.randint(0, 32767)
    }
    resp = klippy_command(payload, socket_path=socket_path)
    if not resp:
        return None
    try:
        hubs = resp["result"]["status"]["filament_hub"]["filament_hubs"]
    except (KeyError, TypeError):
        return None
    for hub in hubs:
        if hub.get("id") == ace_id:
            return hub.get("dryer_status", {}).get("status")
    return None

def update_app_json(ups_name="", ups_status="UK", battery_charge="", nut_address="", nut_port=3493, nut_user="", nut_password=""):
    try:
        with open("app.json.default", 'r') as f:
            app_data = json.load(f)
    except Exception as e:
        raise Exception(f"Error reading app.json.default: {e}")
    else:
        status_map = {
            "OL": "Online",
            "OB": "On battery",
            "UK": "Unknown"
        }
        app_data["properties"]["ups_name"]["default"] = ups_name
        app_data["properties"]["ups_status"]["default"] = status_map.get(ups_status, ups_status)
        app_data["properties"]["battery_charge"]["default"] = battery_charge
        app_data["properties"]["nut_address"]["default"] = nut_address
        app_data["properties"]["nut_port"]["default"] = nut_port
        app_data["properties"]["nut_user"]["default"] = nut_user
        app_data["properties"]["nut_password"]["default"] = nut_password
    try:
        with open("app.json", 'w') as f:
            json.dump(app_data, f, indent=4)
    except Exception as e:
        raise Exception(f"Error writing to app.json: {e}")
    else:
        return True

def get_print_status():
    try:
        response = requests.get("http://localhost:7125/printer/objects/query?print_stats", timeout=5)
        response.raise_for_status()
        data = response.json()
        return data["result"]["status"]["print_stats"]["state"]
    except Exception as e:
        print(f"Error fetching print status: {e}")
        return None

def get_nozzle_target():
    try:
        response = requests.get("http://localhost:7125/printer/objects/query?extruder", timeout=5)
        response.raise_for_status()
        data = response.json()
        return data["result"]["status"]["extruder"]["target"]
    except Exception as e:
        print(f"Error fetching target nozzle temperature: {e}")
        return None

def get_bed_target():
    try:
        response = requests.get("http://localhost:7125/printer/objects/query?heater_bed", timeout=5)
        response.raise_for_status()
        data = response.json()
        return data["result"]["status"]["heater_bed"]["target"]
    except Exception as e:
        print(f"Error fetching target bed temperature: {e}")
        return None

def pause_print():
    try:
        response = requests.post("http://localhost:7125/printer/print/pause", timeout=30)
        response.raise_for_status()
        print("Print paused successfully.")
        return response.json()
    except Exception as e:
        print(f"Error pausing print: {e}")
        return None

def resume_print():
    try:
        response = requests.post("http://localhost:7125/printer/print/resume", timeout=30)
        response.raise_for_status()
        print("Print resumed successfully.")
        return response.json()
    except Exception as e:
        print(f"Error resuming print: {e}")
        return None

def send_gcode_script(script):
    try:
        url = "http://localhost:7125/printer/gcode/script"
        params = {"script": script}
        response = requests.post(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("result", None)
    except Exception as e:
        print(f"Error sending G-code script: {e}")
        return None

def set_nozzle_target(nozzle_target_temp):
    print(f"Setting nozzle target temperature to {nozzle_target_temp}C")
    return send_gcode_script(f"M104 S{nozzle_target_temp}")

def set_bed_target(bed_target_temp):
    print(f"Setting bed target temperature to {bed_target_temp}C")
    return send_gcode_script(f"M140 S{bed_target_temp}")

def stop_ace_drying(ace_id, socket_path="/tmp/unix_uds1"):
    response = klippy_command({
        "method": "filament_hub/stop_drying",
        "params": {
            "id": ace_id
        },
        "id": random.randint(1, 32767)
    })
    return response

# For now, until we support multiple UPS's, as it is possible to have the ACE Pro
# attached to it's own dedicated UPS the drying will not be resumed automatically.
# def resume_ace_drying(ace_id, duration, temp, socket_path="/tmp/unix_uds1"):
    # return True

### Main start ###
signal.signal(signal.SIGINT, handler)
signal.signal(signal.SIGTERM, handler)
sock = socket.socket()
ups_vars = []

try:
    update_app_json()
    read_config_file()
    connect(sock, nut_address, nut_port)
    if nut_user or nut_password:
        login(sock, nut_user, nut_password)

    if not ups_name:
        ups_name = auto_select_ups(sock)
        if ups_name == "No UPS found":
            print("No UPS found")
            sys.exit(1)
        elif ups_name == "No data received":
            print("No data received from LIST UPS")
            sys.exit(1)

    ace_ids = get_ace_pro_ids()
    print(f"Found ACE Pro IDs: {ace_ids}")
    ups_status = read_ups_var(sock, ups_name, "ups.status")
    battery_charge = read_ups_var(sock, ups_name, "battery.charge")
    prev_ups_status = ups_status
    prev_battery_charge = battery_charge
    update_app_json(ups_name, ups_status, battery_charge, nut_address, nut_port, nut_user, nut_password)

    while True:
        print(ups_status)
        print(battery_charge)
        if is_on_printer:
            print(f"Print status: {get_print_status()}")
            print(f"Nozzle: {get_nozzle_target()}")
            print(f"Bed: {get_bed_target()}")
            for ace_id in ace_ids:
                print(f"ACE Pro ID: {ace_id}")
                status = get_ace_pro_status(ace_id)
                if status:
                    print(f"ACE Pro {ace_id} status: {status}")
                else:
                    print(f"ACE Pro {ace_id} not found or no status available")
                #set_nozzle_target(0)
            #set_bed_target(0)

        if ( ups_status != prev_ups_status or battery_charge != prev_battery_charge ):
            prev_ups_status = ups_status
            prev_battery_charge = battery_charge
            update_app_json(ups_name, ups_status, battery_charge, nut_address, nut_port, nut_user, nut_password)
            print(f"UPS status changed from {prev_ups_status} to {ups_status}")
            if ups_status == "OB":
                print("UPS is on battery power!")
                print("Pausing print")
                # Something to pause the print
                print("Turning off nozzle heater")
                # Something to turn off the nozzle heater
            if ups_status == "OL":
                print("UPS is back online!")
            if battery_charge >= "90":
                print("Turning on nozzle heater")
                # Something to turn on the nozzle heater
                print("Resuming print")
                # Something to resume the print
        time.sleep(5)
        ups_status = read_ups_var(sock, ups_name, "ups.status")
        ups_charge = read_ups_var(sock, ups_name, "battery.charge")

except KeyboardInterrupt:
    print("\nInterrupted by user, shutting down...")
    sys.exit(0)
except Exception as e:
    print(f"An error occurred: {e}", file=sys.stderr)    
    sys.exit(1)
finally:
    update_app_json()
    print("Closing socket connection...")
    sock.close()

##############
# Notes
##############
#
# Check nozzle temp
# curl -s "http://localhost:7125/printer/objects/query?extruder&heater_bed" | jq -r '.result.status.extruder.temperature'
# 36
#
# Check nozzle target temp
# curl -s "http://localhost:7125/printer/objects/query?extruder&heater_bed" | jq -r '.result.status.extruder.target'
# 0
#
# Check print status
# root@Rockchip:/root# curl -s "http://localhost:7125/printer/objects/query?print_stats" | jq -r '.result.status.print_stats.state'
# printing
## or
# paused
#
# Set nozzle temp
# curl -sX POST "http://localhost:7125/printer/gcode/script?script=M104%20S200" | jq -r '.result'
# ok
#
# Pause print
# curl -sX POST "http://localhost:7125/printer/print/pause"
# No output due to -s
#
# Resume print
# curl -sX POST "http://localhost:7125/printer/print/resume"
# No output due to -s
# 
## Ace Pro control: ##
# These tests use the klippy-command.sh script in the web-ui app to send commands to Klipper.
# It should be possible to replace this with Python socket connections.
#
# Check ACE ids to see if one or more is connected.
# root@Rockchip:/tmp/nut# bash klippy-command.sh  "{\"method\":\"objects/query\",\"params\":{\"objects\":{\"filament_hub\":null}},\"id\":$RANDOM}" | jq -r '.result.status.filament_hub.filament_hubs[].id'
# 0
#
# Check ACE Pro status
# root@Rockchip:/tmp/nut# bash klippy-command.sh  "{\"method\":\"objects/query\",\"params\":{\"objects\":{\"filament_hub\":null}},\"id\":$RANDOM}" | jq -r '.result.status.filament_hub.filament_hubs[0].dryer_status'
# {
#   "status": "drying",
#   "target_temp": 45,
#   "duration": 240,
#   "remain_time": 13303
# }
#
# Stop drying:
# root@Rockchip:/tmp/nut# bash klippy-command.sh  "{\"method\":\"filament_hub/stop_drying\",\"params\":{\"id\":0},\"id\":$RANDOM}"
# {"id":32079,"result":{}}
#
# Stop drying, look for result:
# root@Rockchip:/tmp/nut# bash klippy-command.sh  "{\"method\":\"filament_hub/stop_drying\",\"params\":{\"id\":0},\"id\":$RANDOM}" | jq -e ".result == {}"
# true
#
# Start drying:
# !/bin/bash
# ace_id=$1
# duration=$2
# temp=$3
# 
# cd "$(dirname "$0")"
# ./klippy-command.sh "{\"method\":\"filament_hub/start_drying\",\"params\":{\"duration\":$duration,\"fan_speed\":0,\"id\":$ace_id,\"temp\":$temp},\"id\":$RANDOM}" | jq -e ".result == {}"
#
#
### Working with App Properties ###
#
#    RESOLUTION=$(get_app_property 30-mjpg-streamer resolution)
#    if [ "$RESOLUTION" != "" ]; then
#        RESOLUTION="-r $RESOLUTION"
#    else
#        RESOLUTION="-r 1280x720"
#    fi
#
            # environment = shell(f'. /useremain/rinkhals/.current/tools.sh && python -c "import os, json; print(json.dumps(dict(os.environ)))"')
            # environment = json.loads(environment)

            # self.KOBRA_MODEL_ID = environment['KOBRA_MODEL_ID']
            # self.KOBRA_MODEL_CODE = environment['KOBRA_MODEL_CODE']
            # self.KOBRA_DEVICE_ID = environment['KOBRA_DEVICE_ID']

        #     def load_tool_function(function_name):
        #         def tool_function(*args):
        #             return shell(f'. /useremain/rinkhals/.current/tools.sh && {function_name} ' + ' '.join([ str(a) for a in args ]))
        #         return tool_function
            
        #     self.get_app_property = load_tool_function('get_app_property')
        # except:
        #     pass
#
### UPS Load testing
### These tests were done with a 1000W CyberPower UPS and a Kobra 3 Max with Ace turned off watching ups.load from NUT. Active print was with TPU at 207C nozzle temp and 60C bed temp and garage temp ~85F.
### Kobra 3 typically peaks around 900-1000W during initial bed heating at start of job with Ace not activly drying.
### Load during active printing: 25-40%
### Load during pause, nozzle set to 140C, bed maintaining 60C: 10-30% while maintaining both tempratures after nozzle cooled down.
### Load after manually disabling nozzle heater: 10-20% to maintain bed temp.
### Load on resume with nozzle heating back up to print temp: peaked around 30%, but I suspect this was low because the nozzle heated up fast and NUT only pools every 30 seconds.
###
### These tests will need to be repeated later with Ace drying turned on and with a more responsive load monitor such as the UPS screen or another device like a Kill-A-Watt or generic Amp Meter.
#
##############