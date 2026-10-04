param(
    [Parameter(Mandatory = $true)][string]$Distro,
    [ValidateRange(1, 1024)][int]$MinimumFreeGiB = 20
)
$ErrorActionPreference = 'Stop'
$distribution = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' |
    Get-ItemProperty | Where-Object { $_.DistributionName -eq $Distro }
if (@($distribution).Count -ne 1) {
    throw "Cannot find the WSL storage location for '$Distro' in the current Windows account."
}
$basePath = $distribution.BasePath
if ($basePath -notmatch '^(?:\\\\\?\\)?([A-Za-z]):\\') {
    throw "Cannot determine the host drive for WSL storage: $basePath"
}
$drive = [System.IO.DriveInfo]::new($Matches[1] + ':\')
$freeGiB = $drive.AvailableFreeSpace / 1GB
Write-Output ("WSL {0}: storage={1}; host free={2:N1} GiB; required={3} GiB" -f $Distro, $basePath, $freeGiB, $MinimumFreeGiB)
if ($freeGiB -lt $MinimumFreeGiB) {
    throw 'Insufficient host disk space for the full gem5 build. Free space or move the WSL distribution before retrying; Linux df alone does not measure host VHD capacity.'
}
