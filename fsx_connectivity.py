import boto3
import sys
from botocore.exceptions import ClientError


def resolve_fsx(environment, stack_number, region):
    """
    Given an environment (qa/uat/prod) and a stack number, find the matching
    FSX file system. Matches on the Name tag using the pattern:
    <environment>-<stack_number>-FSX (case-insensitive).
    """
    client = boto3.client('fsx', region_name=region)
    expected_name = f"{environment}-{stack_number}-fsx"

    paginator = client.get_paginator('describe_file_systems')
    matches = []

    for page in paginator.paginate():
        for fs in page['FileSystems']:
            tags = {t['Key']: t['Value'] for t in fs.get('Tags', [])}
            name = tags.get('Name', '')
            if name.lower() == expected_name:
                matches.append(fs)

    if not matches:
        raise ValueError(
            f"No FSX file system found matching name '{expected_name}' in region '{region}'."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Multiple FSX file systems found matching name '{expected_name}'. Expected exactly one."
        )

    fs = matches[0]
    return {
        'FileSystemId': fs['FileSystemId'],
        'Name': expected_name,
        'NetworkInterfaceIds': fs['NetworkInterfaceIds'],
        'DNSName': fs['DNSName'],
    }


def get_apps_security_group(network_interface_ids, region):
    """
    Given a list of ENI IDs (from an FSX file system), find the security
    group whose name contains 'AppsSecurityGroup'. Raises if none or more
    than one is found across the provided ENIs.
    """
    client = boto3.client('ec2', region_name=region)
    response = client.describe_network_interfaces(NetworkInterfaceIds=network_interface_ids)

    matches = []
    for eni in response['NetworkInterfaces']:
        for group in eni.get('Groups', []):
            if 'appssecuritygroup' in group['GroupName'].lower():
                matches.append({
                    'GroupId': group['GroupId'],
                    'GroupName': group['GroupName'],
                    'NetworkInterfaceId': eni['NetworkInterfaceId'],
                })

    if not matches:
        raise ValueError(
            f"No AppsSecurityGroup found on network interfaces {network_interface_ids}."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Multiple AppsSecurityGroup matches found on network interfaces {network_interface_ids}. Expected exactly one."
        )

    return matches[0]


def resolve_stack(environment, stack_number, region, label):
    """
    Resolve a single stack's FSX and AppsSecurityGroup, with a label
    (e.g. 'OLD' or 'NEW') used only for clearer print output.
    """
    fsx = resolve_fsx(environment, stack_number, region)
    print(f"[{label}] Resolved FSX: {fsx}")

    security_group = get_apps_security_group(fsx['NetworkInterfaceIds'], region)
    print(f"[{label}] Resolved Security Group: {security_group}")

    return {
        'fsx': fsx,
        'security_group': security_group,
    }


def add_cross_stack_rule(target_sg_id, source_sg_id, description, region):
    """
    Add an inbound rule to target_sg_id allowing all traffic from
    source_sg_id, tagged with the given description (e.g. the Jira ticket
    number). If the identical rule already exists, this is treated as a
    success, not an error, so the script is safe to re-run.
    """
    client = boto3.client('ec2', region_name=region)

    try:
        client.authorize_security_group_ingress(
            GroupId=target_sg_id,
            IpPermissions=[
                {
                    'IpProtocol': '-1',
                    'UserIdGroupPairs': [
                        {
                            'GroupId': source_sg_id,
                            'Description': description,
                        }
                    ],
                }
            ],
        )
        print(f"Added rule on {target_sg_id}: allow all traffic from {source_sg_id} ({description})")
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidPermission.Duplicate':
            print(f"Rule already exists on {target_sg_id} allowing traffic from {source_sg_id}, skipping.")
        else:
            raise


def remove_cross_stack_rule(target_sg_id, source_sg_id, description, region):
    """
    Remove the inbound rule on target_sg_id that allows all traffic from
    source_sg_id, matching the given description (e.g. the Jira ticket
    number). If no matching rule exists, this is treated as a success,
    not an error, so the script is safe to re-run.
    """
    client = boto3.client('ec2', region_name=region)

    try:
        client.revoke_security_group_ingress(
            GroupId=target_sg_id,
            IpPermissions=[
                {
                    'IpProtocol': '-1',
                    'UserIdGroupPairs': [
                        {
                            'GroupId': source_sg_id,
                            'Description': description,
                        }
                    ],
                }
            ],
        )
        print(f"Removed rule on {target_sg_id}: revoked traffic from {source_sg_id} ({description})")
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidPermission.NotFound':
            print(f"No matching rule found on {target_sg_id} for {source_sg_id} ({description}), skipping.")
        else:
            raise


def resolve_processing_instance(environment, stack_number, region, label):
    """
    Resolve the processing EC2 instance for a given stack via CloudFormation,
    matching the approach used in the stack deployment pipeline
    (describe-stack-resources, logical resource ID 'ProcessingInstance').
    """
    stack_name = f"{environment}-{stack_number}"
    client = boto3.client('cloudformation', region_name=region)

    try:
        response = client.describe_stack_resource(
            StackName=stack_name,
            LogicalResourceId='ProcessingInstance',
        )
    except ClientError as e:
        raise ValueError(
            f"Could not resolve processing instance for stack '{stack_name}': {e}"
        )

    instance_id = response['StackResourceDetail']['PhysicalResourceId']
    print(f"[{label}] Resolved processing instance for '{stack_name}': {instance_id}")
    return instance_id


def run_ssm_command(instance_id, command, region, timeout_seconds=60):
    """
    Run a PowerShell command on the given instance via SSM and wait for it
    to complete. Raises if the command does not finish successfully.
    """
    client = boto3.client('ssm', region_name=region)

    send_response = client.send_command(
        InstanceIds=[instance_id],
        DocumentName='AWS-RunPowerShellScript',
        Parameters={'commands': [command]},
        TimeoutSeconds=timeout_seconds,
    )
    command_id = send_response['Command']['CommandId']

    waiter = client.get_waiter('command_executed')
    try:
        waiter.wait(
            CommandId=command_id,
            InstanceId=instance_id,
            WaiterConfig={'Delay': 3, 'MaxAttempts': 20},
        )
    except Exception:
        pass  # fall through, we check status explicitly below regardless

    invocation = client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
    status = invocation['Status']
    stdout = invocation.get('StandardOutputContent', '')
    stderr = invocation.get('StandardErrorContent', '')

    if status != 'Success':
        raise ValueError(f"SSM command failed on {instance_id} (status={status}): {stderr or stdout}")

    return stdout


def create_symlink(instance_id, symlink_name, target_dns_name, region):
    """
    Create a directory symlink on the given instance's public desktop,
    pointing at the given FSX DNS name's share. Safe to re-run; if the
    symlink already exists, this is treated as a success.
    """
    command = (
        f"$linkPath = 'C:\\Users\\Public\\Desktop\\{symlink_name}'; "
        f"$target = '\\\\{target_dns_name}\\share'; "
        f"if (Test-Path $linkPath) {{ Write-Output 'Symlink already exists, skipping.' }} "
        f"else {{ cmd /c mklink /D \"$linkPath\" \"$target\"; Write-Output 'Symlink created.' }}"
    )
    output = run_ssm_command(instance_id, command, region)
    print(f"[{instance_id}] {output.strip()}")


def remove_symlink(instance_id, symlink_name, region):
    """
    Remove a directory symlink from the given instance's public desktop.
    Safe to re-run; if the symlink does not exist, this is treated as a
    success.
    """
    command = (
        f"$linkPath = 'C:\\Users\\Public\\Desktop\\{symlink_name}'; "
        f"if (Test-Path $linkPath) {{ cmd /c rmdir \"$linkPath\"; Write-Output 'Symlink removed.' }} "
        f"else {{ Write-Output 'Symlink not found, skipping.' }}"
    )
    output = run_ssm_command(instance_id, command, region)
    print(f"[{instance_id}] {output.strip()}")


if __name__ == '__main__':
    if len(sys.argv) != 7:
        print("Usage: python fsx_connectivity.py <environment> <old_stack_number> <new_stack_number> <region> <jira_ticket_number> <action:ENABLE|DISABLE>")
        sys.exit(1)

    environment, old_stack_number, new_stack_number, region, jira_ticket_number, action = (
        sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6].upper()
    )

    if action not in ('ENABLE', 'DISABLE'):
        print(f"Error: ACTION must be ENABLE or DISABLE, got '{action}'.")
        sys.exit(1)

    try:
        old = resolve_stack(environment, old_stack_number, region, 'OLD')
        new = resolve_stack(environment, new_stack_number, region, 'NEW')

        old_sg_id = old['security_group']['GroupId']
        new_sg_id = new['security_group']['GroupId']
        description = f"TEMP {jira_ticket_number}"

        print(f"OLD SG: {old_sg_id}")
        print(f"NEW SG: {new_sg_id}")
        print(f"ACTION: {action}")

        old_symlink_name = f"{environment}_{new_stack_number}_netshare"
        new_symlink_name = f"{environment}_{old_stack_number}_netshare"

        if action == 'ENABLE':
            add_cross_stack_rule(old_sg_id, new_sg_id, description, region)
            add_cross_stack_rule(new_sg_id, old_sg_id, description, region)

            old_instance_id = resolve_processing_instance(environment, old_stack_number, region, 'OLD')
            new_instance_id = resolve_processing_instance(environment, new_stack_number, region, 'NEW')

            create_symlink(old_instance_id, old_symlink_name, new['fsx']['DNSName'], region)
            create_symlink(new_instance_id, new_symlink_name, old['fsx']['DNSName'], region)
        else:
            old_instance_id = resolve_processing_instance(environment, old_stack_number, region, 'OLD')
            new_instance_id = resolve_processing_instance(environment, new_stack_number, region, 'NEW')

            remove_symlink(old_instance_id, old_symlink_name, region)
            remove_symlink(new_instance_id, new_symlink_name, region)

            remove_cross_stack_rule(old_sg_id, new_sg_id, description, region)
            remove_cross_stack_rule(new_sg_id, old_sg_id, description, region)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)