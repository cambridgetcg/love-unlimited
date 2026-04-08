#!/usr/bin/env python3

import argparse
import sys
import time
from datetime import datetime, timezone

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    print("Error: boto3 not installed. Run: pip3 install boto3")
    sys.exit(1)

ACCOUNT_ID = "034362054546"
INSTANCE_TYPE = "g6e.2xlarge"
ELASTIC_IP = "52.7.131.246"
REGION = "us-east-1"
HOURLY_RATE = 1.58

def get_ec2_client():
    try:
        return boto3.client('ec2', region_name=REGION)
    except NoCredentialsError:
        print("Error: AWS credentials not found")
        sys.exit(1)

def find_brain_instance(ec2):
    try:
        response = ec2.describe_instances(
            Filters=[
                {'Name': 'instance-type', 'Values': [INSTANCE_TYPE]},
                {'Name': 'instance-state-name', 'Values': ['running', 'stopped', 'stopping', 'pending']}
            ]
        )

        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                for addr in instance.get('Addresses', []):
                    if addr.get('PublicIp') == ELASTIC_IP:
                        return instance

                if instance.get('PublicIpAddress') == ELASTIC_IP:
                    return instance

                for ni in instance.get('NetworkInterfaces', []):
                    if ni.get('Association', {}).get('PublicIp') == ELASTIC_IP:
                        return instance

        if response['Reservations']:
            return response['Reservations'][0]['Instances'][0]

        print(f"Error: No {INSTANCE_TYPE} instance found")
        sys.exit(1)
    except ClientError as e:
        print(f"AWS API Error: {e}")
        sys.exit(1)

def format_uptime(launch_time):
    if not launch_time:
        return "N/A"

    now = datetime.now(timezone.utc)
    uptime = now - launch_time

    days = uptime.days
    hours = uptime.seconds // 3600
    minutes = (uptime.seconds % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")

    return " ".join(parts)

def cmd_status(args):
    ec2 = get_ec2_client()
    instance = find_brain_instance(ec2)

    instance_id = instance['InstanceId']
    state = instance['State']['Name']
    instance_type = instance['InstanceType']
    public_ip = instance.get('PublicIpAddress', 'N/A')

    launch_time = instance.get('LaunchTime')
    uptime = format_uptime(launch_time) if state == 'running' else 'N/A'

    name = 'N/A'
    for tag in instance.get('Tags', []):
        if tag['Key'] == 'Name':
            name = tag['Value']
            break

    print(f"Brain Instance Status")
    print(f"  ID:         {instance_id}")
    print(f"  Name:       {name}")
    print(f"  Type:       {instance_type}")
    print(f"  State:      {state}")
    print(f"  IP:         {public_ip}")
    print(f"  Elastic IP: {ELASTIC_IP}")
    print(f"  Region:     {REGION}")
    print(f"  Uptime:     {uptime}")

def cmd_start(args):
    ec2 = get_ec2_client()
    instance = find_brain_instance(ec2)

    instance_id = instance['InstanceId']
    state = instance['State']['Name']

    if state == 'running':
        print(f"Instance {instance_id} is already running")
        return

    if state == 'pending':
        print(f"Instance {instance_id} is already starting")
        return

    print(f"Starting instance {instance_id}...")

    try:
        ec2.start_instances(InstanceIds=[instance_id])

        print("Waiting for instance to start...", end="", flush=True)
        waiter = ec2.get_waiter('instance_running')
        waiter.wait(InstanceIds=[instance_id])
        print(" done")

        instance = find_brain_instance(ec2)
        public_ip = instance.get('PublicIpAddress', 'N/A')
        print(f"Instance started successfully")
        print(f"  State: {instance['State']['Name']}")
        print(f"  IP:    {public_ip}")
    except ClientError as e:
        print(f"\nError starting instance: {e}")
        sys.exit(1)

def cmd_stop(args):
    ec2 = get_ec2_client()
    instance = find_brain_instance(ec2)

    instance_id = instance['InstanceId']
    state = instance['State']['Name']

    if state in ['stopped', 'stopping']:
        print(f"Instance {instance_id} is already {state}")
        return

    if not args.force:
        response = input(f"Stop instance {instance_id}? [y/N]: ")
        if response.lower() not in ['y', 'yes']:
            print("Cancelled")
            return

    print(f"Stopping instance {instance_id}...")

    try:
        ec2.stop_instances(InstanceIds=[instance_id])
        print("Stop request sent successfully")
    except ClientError as e:
        print(f"Error stopping instance: {e}")
        sys.exit(1)

def cmd_cost(args):
    print(f"Brain Instance Cost Estimate ({INSTANCE_TYPE})")
    print(f"  Hourly:  ${HOURLY_RATE:.2f}")
    print(f"  Daily:   ${HOURLY_RATE * 24:.2f}")
    print(f"  Weekly:  ${HOURLY_RATE * 24 * 7:.2f}")
    print(f"  Monthly: ${HOURLY_RATE * 24 * 30:.2f}")
    print()
    print(f"Region: {REGION} (on-demand pricing)")

def main():
    parser = argparse.ArgumentParser(description="Manage AWS GPU Brain instance")
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    subparsers.add_parser('status', help='Show instance status')
    subparsers.add_parser('start', help='Start the instance')

    stop_parser = subparsers.add_parser('stop', help='Stop the instance')
    stop_parser.add_argument('--force', action='store_true', help='Skip confirmation')

    subparsers.add_parser('cost', help='Show cost estimates')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        'status': cmd_status,
        'start': cmd_start,
        'stop': cmd_stop,
        'cost': cmd_cost,
    }

    commands[args.command](args)

if __name__ == '__main__':
    main()
