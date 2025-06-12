#!/usr/bin/env python3

import configparser
import json
import random
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
        global ups_name, address, port, user, password, is_on_printer
        section = config["nut"]
        ups_name = section.get('ups_name')  # Need clean error for when configured UPS does not exist
        address = section.get('address') or "localhost"
        port = int(section.get('port') or 3493)
        user = section.get('user')
        password = section.get('password')
        is_on_printer = section.get('is_on_printer', 'false').lower() in ('true', '1', 'yes') or False  # For testing outside of printer
        return True

def connect(sock, address, port=3493):
    try:
        sock.settimeout(10)
        sock.connect((address, port))
    except Exception as e:
        sock.close()
        raise Exception(f"Could not connect to {address}:{port} - {e}")
    else:
        return True

def login(sock, user=None, password=None, timeout=5):
    sock.settimeout(timeout)
    try: 
        if user:
            sock.sendall(f"USERNAME {user}\n".encode("utf-8"))
            resp = recv_line(sock)
            if resp != b"OK":
                text = resp.decode("utf-8", errors="replace")
                raise ValueError(f"Username not accepted, server replied: {text!r}")
            print(f"Username accepted")
        if password:
            sock.sendall(f"PASSWORD {password}\n".encode("utf-8"))
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
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
        sock.sendall(msg)
        sock.shutdown(socket.SHUT_WR)
        data = bytearray()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if b'\x03' in chunk:
                break
        sock.shutdown(socket.SHUT_RD)
    finally:
        sock.close()
    raw = data.rstrip(b'\x03')
    if not raw:
        return False
    return json.loads(raw.decode('utf-8', errors="replace"))

def get_ace_pro_ids(socket_path="/tmp/unix_uds1"):
    payload = {
        "method": "objects/query",
        "params": {"objects": {"filament_hub": None}},
        "id": random.randint(0, 32767)
    }
    resp = klippy_command(payload)
    if not resp or 'result' not in resp or 'status' not in resp or 'filament_hub' not in resp['result']['status']:
        return []
    hubs = resp.get('result', {}) \
               .get('status', {}) \
               .get('filament_hub', {}) \
               .get('filament_hubs', [])
    return [h.get('id') for h in hubs]    

def get_ace_pro_status(ace_id, socket_path="/tmp/unix_uds1"):
    payload = {
        "method": "objects/query",
        "params": {"objects": {"filament_hub": None}},
        "id": random.randint(0, 32767)
    }
    resp = klippy_command(payload)
    if not resp or 'result' not in resp or 'status' not in resp or 'filament_hub' not in resp['result']['status']:
        return []
    hubs = resp.get('result', {}) \
               .get('status', {}) \
               .get('filament_hub', {}) \
               .get('filament_hubs', [])
    for hub in hubs:
        if hub.get('id') == ace_id:
            return hub.get('dryer_status', {})
    return None

### Main start ###
signal.signal(signal.SIGINT, handler)
signal.signal(signal.SIGTERM, handler)
sock = socket.socket()
ups_vars = []

try:
    read_config_file()
    connect(sock, address, port)
    if user or password:
        login(sock, user, password)

    if is_on_printer:
        for ace_id in get_ace_pro_ids():
            print(f"ACE Pro ID: {ace_id}")
            status = get_ace_pro_status(ace_id)
            if status:
                print(f"ACE Pro {ace_id} status: {status}")
            else:
                print(f"ACE Pro {ace_id} not found or no status available")

    if not ups_name:
        ups_name = auto_select_ups(sock)
        if ups_name == "No UPS found":
            print("No UPS found")
            sys.exit(1)
        elif ups_name == "No data received":
            print("No data received from LIST UPS")
            sys.exit(1)

    ups_status = read_ups_var(sock, ups_name, "ups.status")
    prev_ups_status = ups_status
    ups_charge = read_ups_var(sock, ups_name, "battery.charge")
    while True:
        ups_status = read_ups_var(sock, ups_name, "ups.status")
        ups_charge = read_ups_var(sock, ups_name, "battery.charge")
        print(ups_status)
        print(ups_charge)
        read_ups_vars(sock, ups_name, ups_vars)
        #print(ups_vars)
        if ups_status != prev_ups_status:
            print(f"UPS status changed from {prev_ups_status} to {ups_status}")
            prev_ups_status = ups_status
            if ups_status == "On Battery":
                print("UPS is on battery power!")
                print("Pausing print")
                # Something to pause the print
                print("Turning off nozzle heater")
                # Something to turn off the nozzle heater
            if ups_status == "Online":
                print("UPS is back online!")
            if ups_charge >= "90":
                print("Turning on nozzle heater")
                # Something to turn on the nozzle heater
                print("Resuming print")
                # Something to resume the print
        time.sleep(5)

except KeyboardInterrupt:
    print("\nInterrupted by user, shutting down...")
    sys.exit(0)
except Exception as e:
    print(f"An error occurred: {e}", file=sys.stderr)    
    sys.exit(1)
finally:
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