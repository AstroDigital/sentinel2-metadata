import subprocess
from iron_worker import IronWorker


def main():
    payload = IronWorker.payload()

    if 'es-host' not in payload:
        raise Exception('elastic search host is missing')

    host = payload['es-host']
    port = payload.get('es-port', 80)
    start = payload.get('start-date', None)
    end = payload.get('end-date', None)

    cmd = ['python', 'main.py',
           'es', '--verbose',
           '--es-host', host,
           '--es-port', port]

    if start:
        cmd.extend(['--start', start])

    if end:
        cmd.extend(['--end', end])

    subprocess(cmd)

if __name__ == '__main__':
    main()
