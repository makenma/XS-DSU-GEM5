$ErrorActionPreference = 'Stop'
$registered = Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' |
    ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath } |
    Where-Object { $_.DistributionName -eq 'Ubuntu' }
if ($registered.BasePath -ne 'D:\wsl\Ubuntu') { throw 'Unexpected Ubuntu VHD location.' }
$swap = Get-Item -LiteralPath 'D:\wsl\swap.vhdx'
$oldSwap = 'C:\Users\庞欣仪\AppData\Local\Temp\9A3CDF1D-7B0E-4CA9-87A2-BA58625F5DF7\swap.vhdx'
$report = [ordered]@{
    observedAt = [DateTimeOffset]::Now.ToString('o')
    ubuntuBasePath = $registered.BasePath
    rootVhd = Join-Path $registered.BasePath $registered.VhdFileName
    swapFile = $swap.FullName
    swapPhysicalBytes = $swap.Length
    previousCSwapStillExists = Test-Path -LiteralPath $oldSwap
    projectPath = 'D:\proj\chi_flowgraph'
    linuxBuildPath = '/root/chi-flowgraph-work/gem5'
    linuxBuildStorage = 'Inside D:\wsl\Ubuntu\ext4.vhdx'
    windowsProfileConfigBytes = (Get-Item -LiteralPath 'C:\Users\庞欣仪\.wslconfig').Length
    wslRestartedAfterRegressionCompleted = $true
    stoppedLiveSimulation = $false
}
$text = $report | ConvertTo-Json
[System.IO.File]::WriteAllText('D:\proj\chi_flowgraph\build\qa\wsl-d-drive-storage\verified.json',
    $text, (New-Object System.Text.UTF8Encoding($false)))
$text
