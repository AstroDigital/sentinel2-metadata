import os
import subprocess
from iron_worker import IronWorker


def main():
    payload = IronWorker.payload()

    args = payload.get('args')

    aws_id = payload.get('aws-access-id')
    aws_secret = payload.get('aws-secret-key')

    cmd = ['python', 'main.py']

    if not args:
        raise Exception('args is missing')

    cmd.extend(args.split(' '))

    if not (aws_id and aws_secret):
        raise Exception('missing argument. aws-access-id and aws-secret-key are required')

    os.environ['AWS_ACCESS_KEY_ID'] = aws_id
    os.environ['AWS_SECRET_ACCESS_KEY'] = aws_secret
    subprocess.call(cmd)

if __name__ == '__main__':
    main()
