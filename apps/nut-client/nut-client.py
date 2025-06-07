#!/usr/bin/env python3

import socket
import sys
import time

address = "localhost"
port = 3493
user = ""
password = ""
ups_name = ""

def nut_login(socket, user, password):
    if user:
        socket.send(f"USERNAME {user}\n".encode('utf-8'))
        response = socket.recv(64)
        if response != b"OK\n":
            print("Username not accepted")
            return False
        print("Username accepted")
    if password:
        socket.send(f"PASSWORD {password}\n".encode('utf-8'))
        response = socket.recv(64)
        if response != b"OK\n":
            print("Password not accepted")
            return False
        print("Password accepted")
    return True

def auto_select_ups(sock, ups_name):
    # For now this automatically selects the first UPS found
    ups_list = []
    sock.send(b"LIST UPS\n")
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
                return (text.split(" ")[1])
        else:
            data = sock.recv(2048)
            if not data:
                return ("No data received")
            buffer += data

def read_ups_vars(sock, ups_name, ups_vars):
    ups_vars.clear()
    sock.send(f"LIST VAR {ups_name}\n".encode('utf-8'))
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
                #print("No more data received")
                return
            elif text.startswith("VAR "):
                #print(text.split(" ")[2:4])
                ups_vars.append(text.split(" ")[2:4])
        else:
            data = sock.recv(2048)
            if not data:
                return ("No data received")
            buffer += data

def read_ups_status(sock, ups_name):
  sock.send(f"GET VAR {ups_name} ups.status\n".encode('utf-8'))
  response = sock.recv(64)
  if "OL" in response.decode('utf-8').split(" ")[3]:
    return "Online"
  if "OB" in response.decode('utf-8').split(" ")[3]:
    return "On Battery"
  else:
    return "Unknown status"
  
def read_ups_charge(sock, ups_name):
  sock.send(f"GET VAR {ups_name} battery.charge\n".encode('utf-8'))
  response = sock.recv(64)
  return response.decode('utf-8').split(" ")[3].split('"')[1]
  
def connect(sock, address, port):
  try:
    sock.settimeout(10)
    sock.connect((address, port))
  except socket.error as e:
    print(f"Could not connect to {address}:{port} - {e}")
    sys.exit(1)
  return True

def list_ups_vars(ups_name, ups_vars):
    print(f"UPS: {ups_name}")
    if not ups_vars:
        print("No variables found")
        return
    for var in ups_vars:
        print(f"{var[0]}: {var[1]}")

sock = socket.socket()
ups_vars = []

if not connect(sock, address, port):
    sys.exit(1)
    
if user or password:
    if not login(sock, user, password):
        sys.exit(1)

if not ups_name:
    ups_name = auto_select_ups(sock,ups_name)
    if ups_name == "No UPS found":
        print("No UPS found")
        sys.exit(1)
    elif ups_name == "No data received":
        print("No data received from LIST UPS")
        sys.exit(1)

ups_status = read_ups_status(sock, ups_name)
prev_ups_status = ups_status
ups_charge = read_ups_charge(sock, ups_name)
while True:
  ups_status = read_ups_status(sock, ups_name)
  print(ups_status)
  print(ups_charge)
  read_ups_vars(sock, ups_name, ups_vars)
  print(ups_vars)
  

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
# Set nozzle temp
# curl -sX POST "http://localhost:7125/printer/gcode/script?script=M104%20S200" | jq -r '.result'
# ok
#
##############
# Notes
##############