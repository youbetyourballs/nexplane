path = '/home/ec2-user/nexplane/agent/executor/executor.go'
lines = open(path).readlines()
print("line37:", repr(lines[37]))
print("line38:", repr(lines[38]))
if lines[37].strip() == '"nexplane-agent/commands/runcommand':
    lines[37] = '\t"nexplane-agent/commands/runcommand"\n'
    lines[38] = '\t"nexplane-agent/commands/appupgrade"\n'
    open(path, 'w').writelines(lines)
    print('fixed')
else:
    print('no match')
