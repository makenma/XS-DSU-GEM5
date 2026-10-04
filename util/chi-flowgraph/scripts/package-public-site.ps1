param([string]$Output = 'build/publish/chi-flowgraph-public.zip')

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dist = Join-Path $projectRoot 'dist'
if (-not (Test-Path -LiteralPath (Join-Path $dist 'index.html'))) {
    throw '缺少 dist/index.html；请先运行 pnpm build。'
}
$outputPath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $Output))
$outputDir = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
if (Test-Path -LiteralPath $outputPath) { Remove-Item -LiteralPath $outputPath }

$files = @(Get-ChildItem -LiteralPath $dist -Recurse -File | Where-Object {
    $_.FullName.Substring($dist.Length + 1).Replace('\', '/') -ne 'benchmarks/100k.chi.jsonl'
})
if ($files.Count -gt 1000) { throw 'Cloudflare Pages 拖拽上传最多支持 1,000 个文件。' }
if (@($files | Where-Object { $_.Length -gt 25MB }).Count) {
    throw '存在超过 Cloudflare Pages 单文件 25 MiB 限制的文件。'
}

$archive = [System.IO.Compression.ZipFile]::Open($outputPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($file in $files) {
        $entry = $file.FullName.Substring($dist.Length + 1).Replace('\', '/')
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $file.FullName, $entry, [System.IO.Compression.CompressionLevel]::Optimal)
    }
} finally {
    $archive.Dispose()
}

$catalog = Get-Content -Raw -LiteralPath (Join-Path $dist 'demos/catalog.json') | ConvertFrom-Json
$names = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
foreach ($file in $files) { [void]$names.Add($file.FullName.Substring($dist.Length + 1).Replace('\', '/')) }
foreach ($demo in $catalog) {
    if (-not $names.Contains("demos/$($demo.file)")) { throw "缺少示例：$($demo.file)" }
}
Write-Output "发布包：$outputPath"
Write-Output "示例：$(@($catalog).Count) 个；文件：$($files.Count) 个；压缩包：$([math]::Round((Get-Item -LiteralPath $outputPath).Length / 1MB, 2)) MiB"
