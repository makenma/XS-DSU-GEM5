$ErrorActionPreference = 'Stop'
$configPath = 'C:\Users\庞欣仪\.wslconfig'
$evidencePath = 'D:\proj\chi_flowgraph\build\qa\wsl-d-drive-storage'
$swapDirectory = 'D:\wsl'
$swapSetting = 'swapFile=D:\\wsl\\swap.vhdx'
$before = [System.IO.File]::ReadAllText($configPath)
if ($before -notmatch '(?im)^\[wsl2\][ \t]*\r?$') {
    throw 'Expected existing [wsl2] section; refusing to rewrite unknown configuration.'
}
if ($before -match '(?im)^\s*swapFile\s*=') {
    $after = [regex]::Replace($before, '(?im)^\s*swapFile\s*=.*$', $swapSetting)
} else {
    $after = [regex]::Replace($before, '(?im)^\[wsl2\][ \t]*\r?$', "[wsl2]`r`n$swapSetting")
}
if (Test-Path -LiteralPath $evidencePath) {
    throw 'Evidence directory already exists; refusing to overwrite the backup.'
}
$null = New-Item -ItemType Directory -Path $evidencePath
if (-not (Test-Path -LiteralPath $swapDirectory)) {
    $null = New-Item -ItemType Directory -Path $swapDirectory
}
Copy-Item -LiteralPath $configPath -Destination (Join-Path $evidencePath 'wslconfig.before')
if ([System.IO.File]::ReadAllText($configPath) -cne $before) {
    throw 'Configuration changed concurrently.'
}
[System.IO.File]::WriteAllText($configPath, $after, (New-Object System.Text.UTF8Encoding($false)))
Copy-Item -LiteralPath $configPath -Destination (Join-Path $evidencePath 'wslconfig.after')
[ordered]@{
    configPath = $configPath
    swapFile = 'D:\wsl\swap.vhdx'
    previousConfigSha256 = (Get-FileHash -LiteralPath (Join-Path $evidencePath 'wslconfig.before') -Algorithm SHA256).Hash
    newConfigSha256 = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash
    previousSettingsPreserved = $true
    wslRestarted = $false
    requiresNewWslVm = $true
} | ConvertTo-Json | Tee-Object -FilePath (Join-Path $evidencePath 'config-change.json')
