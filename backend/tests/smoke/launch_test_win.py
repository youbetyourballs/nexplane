import boto3, base64

ec2 = boto3.client('ec2', region_name='us-east-1')

lines = [
    '<powershell>',
    'net user Administrator "SmokeTest1234!"',
    'Enable-PSRemoting -SkipNetworkProfileCheck -Force',
    r'Set-Item WSMan:\localhost\Service\AllowUnencrypted $true',
    r'Set-Item WSMan:\localhost\Service\Auth\Basic $true',
    'netsh advfirewall firewall add rule name="WinRM HTTP" protocol=TCP dir=in localport=5985 action=allow profile=any',
    '</powershell>',
]
userdata = '\n'.join(lines)
ud_b64 = base64.b64encode(userdata.encode()).decode()

resp = ec2.run_instances(
    ImageId='ami-0ed0165f19a049904',
    InstanceType='t3.medium',
    SubnetId='subnet-051c417c91cc8bf0b',
    SecurityGroupIds=['sg-09f02cfcfcf741211'],
    IamInstanceProfile={'Arn': 'arn:aws:iam::614130399980:instance-profile/NexplaneSmokeSSMProfile'},
    MinCount=1, MaxCount=1,
    UserData=ud_b64,
    TagSpecifications=[{'ResourceType': 'instance', 'Tags': [
        {'Key': 'Name', 'Value': 'np-dc-winrm-test'},
        {'Key': 'ManagedBy', 'Value': 'nexplane-debug'},
    ]}]
)
iid = resp['Instances'][0]['InstanceId']
ip = resp['Instances'][0].get('PrivateIpAddress', 'pending')
print('Launched:', iid, 'ip=', ip)
