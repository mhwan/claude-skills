# Prints only whether each API key is set (True/False), never the value.
# user = saved in Windows user environment variables, app = visible to the running app.
# Kept ASCII-only: Windows PowerShell 5.1 reads BOM-less files as the system code page.
$names = 'LAW_OC', 'DART_API_KEY', 'ASSEMBLY_API_KEY', 'NCP_APIGW_API_KEY_ID', 'NCP_APIGW_API_KEY'
foreach ($name in $names) {
    $user = [Environment]::GetEnvironmentVariable($name, 'User')
    $process = [Environment]::GetEnvironmentVariable($name, 'Process')
    '{0}: user={1}, app={2}' -f $name, [bool]$user, [bool]$process
}
