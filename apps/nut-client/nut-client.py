#!/usr/bin/env python3

import socket
import sys
import time
import configparser
import json
import random

def read_config_file(filename="nut-client-config.ini"):
    config = configparser.ConfigParser()
    try:
        with open(filename, "r") as f:
            config.read_file(f)
    except FileNotFoundError:
        print(f"Config file not found: {filename}", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"Permission denied: {filename}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading config file: {e}", file=sys.stderr)
        sys.exit(1)
    global ups_name, address, port, user, password, is_on_printer
    section = config["nut"]
    ups_name = section.get('ups_name')  # Need clean error for when configured UPS does not exist
    address = section.get('address') or "localhost"
    port = int(section.get('port') or 3493)
    user = section.get('user')
    password = section.get('password')
    is_on_printer = section.get('is_on_printer', 'false').lower() in ('true', '1', 'yes')  # For testing outside of printer
    return True

def connect(sock, address, port):
    try:
        sock.settimeout(10)
        sock.connect((address, port))
    except socket.error as e:
        print(f"Could not connect to {address}:{port} - {e}", file=sys.stderr)
        sys.exit(1)
    return True

def login(socket, user, password):
    if user:
        socket.sendall(f"USERNAME {user}\n".encode('utf-8'))
        response = socket.recv(64)
        if response != b"OK\n":
            print("Username not accepted")
            return False
        print("Username accepted")
    if password:
        socket.sendall(f"PASSWORD {password}\n".encode('utf-8'))
        response = socket.recv(64)
        if response != b"OK\n":
            print("Password not accepted")
            return False
        print("Password accepted")
    return True

def recv_line(sock):
    buffer = b""
    # Read data until we find a newline character
    while b"\n" not in buffer:
        data = sock.recv(256)
        if not data:
            break
        buffer += data
    # Return the first line
    if b"\n" in buffer:
        line, buffer = buffer.split(b"\n", 1)
    else:
        line, buffer = buffer, b""
    return line

def auto_select_ups(sock, ups_name):
    # For now this automatically selects the first UPS found
    ups_list = []
    print("Auto-selecting UPS...")
    sock.sendall(b"LIST UPS\n")
    buffer = b""
    while True:
        data = sock.recv(256)
        if not data:
            return
        buffer += data
        if b"BEGIN LIST UPS\n" in buffer:
            buffer = buffer.split(b"BEGIN LIST UPS\n", 1)[1]
            break
    while True:
        if b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            text = line.decode('utf-8').strip()
            if text == "END LIST UPS":
                return ("No UPS found")
            elif text.startswith("UPS "):
                print("Selecting first found UPS: " + text.split(" ")[1])
                return (text.split(" ")[1])
        else:
            data = sock.recv(2048)
            if not data:
                return ("No data received")
            buffer += data

def read_ups_vars(sock, ups_name, ups_vars):
    ups_vars.clear()
    sock.sendall(f"LIST VAR {ups_name}\n".encode('utf-8'))
    buffer = b""
    while True:
        data = sock.recv(256)
        if not data:
            return
        buffer += data
        marker = f"BEGIN LIST VAR {ups_name}\n".encode("utf-8")
        if marker in buffer:
            buffer = buffer.split(marker, 1)[1]
            break
    while True:
        if b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            text = line.decode('utf-8').strip()
            marker = f"END LIST VAR {ups_name}\n".encode("utf-8")
            if marker in buffer:
                return
            elif text.startswith("VAR "):
                ups_vars.append(text.split(" ")[2:4])
        else:
            data = sock.recv(2048)
            if not data:
                return ("No data received")
            buffer += data

def list_ups_vars(ups_name, ups_vars):
    print(f"UPS: {ups_name}")
    if not ups_vars:
        print("No variables found")
        return
    for var in ups_vars:
        print(f"{var[0]}: {var[1]}")

def read_ups_var(sock, ups_name, var_name):
    sock.sendall(f"GET VAR {ups_name} {var_name}\n".encode('utf-8'))
    return recv_line(sock).decode('utf-8').split()[3].strip('"')

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
    return json.loads(raw.decode('utf-8'))

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

sock = socket.socket()
ups_vars = []

read_config_file()

if is_on_printer:
    for ace_id in get_ace_pro_ids():
        print(f"ACE Pro ID: {ace_id}")
        status = get_ace_pro_status(ace_id)
        if status:
            print(f"ACE Pro {ace_id} status: {status}")
        else:
            print(f"ACE Pro {ace_id} not found or no status available")

connect(sock, address, port)
if user or password: login(sock, user, password) or sys.exit(1)

if not ups_name:
    ups_name = auto_select_ups(sock,ups_name)
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