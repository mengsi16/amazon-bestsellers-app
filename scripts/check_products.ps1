param(
    [Parameter(Mandatory = $true)]
    [string]$BrowseNodeId,

    [int]$Top = 15
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$productsDir = Join-Path $projectRoot "workspace\$BrowseNodeId\products"

Get-ChildItem -LiteralPath $productsDir | Where-Object { $_.Name -ne 'requests.jsonl' } | ForEach-Object {
    $html = Join-Path $_.FullName 'product.html'
    $size = if (Test-Path $html) { (Get-Item $html).Length } else { 0 }
    [PSCustomObject]@{
        ASIN = $_.Name
        SizeKB = [math]::Round($size/1KB, 1)
        HasHTML = (Test-Path $html)
    }
} | Sort-Object SizeKB -Descending | Select-Object -First $Top | Format-Table -AutoSize
