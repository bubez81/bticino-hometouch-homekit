#!/usr/bin/env python3
"""Optional MQTT bridge: entrance lock topics backed by the listener's IPC.

For every entrance configured in the listener it exposes
  <prefix>/<entrance>/set    commands: UNLOCK opens, LOCK only resets state
  <prefix>/<entrance>/state  retained: UNLOCK during the pulse, then LOCK
  <prefix>/status            retained: online / offline (last will)
which matches lock accessories such as Homebridge mqttthing or Home Assistant
MQTT locks. The displayed state is assumed: there is no door sensor.

Retained commands are ignored so a stale UNLOCK can never reopen a door after
a reconnect. Requires paho-mqtt; the listener itself does not.
"""
import argparse
import json
import socket
import threading
import time
from pathlib import Path

DEFAULT_SOCKET = '/tmp/bticino-hometouch.sock'


def ipc_request(path, request, timeout=5):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall((json.dumps(request) + '\n').encode())
        data = b''
        while not data.endswith(b'\n'):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode())


class MqttBridge:
    def __init__(self, client, ipc, entrances, prefix='hometouch', unlock_seconds=3.0,
                 schedule=None, log=print):
        self.client = client
        self.ipc = ipc
        self.entrances = list(entrances)
        self.prefix = prefix.rstrip('/')
        self.unlock_seconds = float(unlock_seconds)
        self.schedule = schedule or (lambda delay, fn: threading.Timer(delay, fn).start())
        self.log = log
        self.pending = set()

    def topic(self, entrance, kind):
        return f'{self.prefix}/{entrance}/{kind}'

    def publish_state(self, entrance, state):
        self.client.publish(self.topic(entrance, 'state'), payload=state, qos=1, retain=True)

    def on_connect(self, *_args):
        self.client.publish(f'{self.prefix}/status', payload='online', qos=1, retain=True)
        for entrance in self.entrances:
            self.client.subscribe(self.topic(entrance, 'set'), qos=1)
            if entrance not in self.pending:
                self.publish_state(entrance, 'LOCK')
        self.log(f'MQTT connesso: {", ".join(self.topic(e, "set") for e in self.entrances)}')

    def on_message(self, _client, _userdata, message):
        if message.retain:
            self.log(f'MQTT comando retained ignorato: {message.topic}')
            return
        entrance = next((e for e in self.entrances if message.topic == self.topic(e, 'set')), None)
        if entrance is None:
            return
        payload = message.payload.decode('utf-8', errors='replace').strip().upper()
        self.log(f'MQTT RX {message.topic} = {payload}')
        if payload == 'UNLOCK':
            self.unlock(entrance)
        elif payload == 'LOCK':
            if entrance not in self.pending:
                self.publish_state(entrance, 'LOCK')
        else:
            self.log(f'MQTT payload ignorato: {payload}')

    def unlock(self, entrance):
        if entrance in self.pending:
            self.log(f'APERTURA {entrance}: già in corso, comando ignorato')
            return
        self.pending.add(entrance)
        self.publish_state(entrance, 'UNLOCK')
        try:
            result = self.ipc({'command': 'open_entrance', 'entrance': entrance})
        except (OSError, ValueError) as error:
            result = {'ok': False, 'error': f'ipc: {error}'}
        if result.get('ok'):
            self.log(f'APERTURA {entrance}: inviata')
            self.schedule(self.unlock_seconds, lambda: self.finish(entrance))
        else:
            self.log(f'APERTURA {entrance}: rifiutata: {result.get("error")}')
            self.finish(entrance)

    def finish(self, entrance):
        self.pending.discard(entrance)
        self.publish_state(entrance, 'LOCK')
        try:
            result = self.ipc({'command': 'entrance_status'}).get('results', {}).get(entrance, {})
            self.log(f'APERTURA {entrance}: pressione={result.get("press")} rilascio={result.get("release")}')
        except (OSError, ValueError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', required=True, type=Path,
                        help='private JSON: host, port, username, password, client_id, prefix, unlock_seconds')
    parser.add_argument('--socket', default=DEFAULT_SOCKET)
    args = parser.parse_args()
    import paho.mqtt.client as mqtt

    def log(message):
        print(time.strftime('%Y-%m-%d %H:%M:%S'), message, flush=True)

    config = json.loads(args.config.read_text(encoding='utf-8'))
    prefix = config.get('prefix', 'hometouch')
    ipc = lambda request: ipc_request(args.socket, request)
    while True:
        try:
            status = ipc({'command': 'entrance_status'})
            if status.get('ok') and status.get('enabled') and status.get('entrances'):
                break
            log(f'Apertura ingressi non attiva nel listener: {status}')
        except (OSError, ValueError) as error:
            log(f'Listener IPC non disponibile: {error}')
        time.sleep(10)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=config.get('client_id', 'bticino-hometouch-mqtt'))
    if config.get('username'):
        client.username_pw_set(config['username'], config.get('password'))
    client.will_set(f'{prefix}/status', payload='offline', qos=1, retain=True)
    bridge = MqttBridge(client, ipc, status['entrances'], prefix,
                        config.get('unlock_seconds', 3.0), log=log)
    client.on_connect = bridge.on_connect
    client.on_message = bridge.on_message
    client.on_disconnect = lambda *a: log('MQTT disconnesso')
    client.reconnect_delay_set(1, 30)
    log(f'Ponte MQTT: {config["host"]}:{config.get("port", 1883)} prefisso {prefix}/ ingressi {status["entrances"]}')
    client.connect(config['host'], int(config.get('port', 1883)), keepalive=60)
    client.loop_forever(retry_first_connection=True)


if __name__ == '__main__':
    main()
