Get-ChildItem 'E:\PostGraduate\Project\amazon-bestsellers\workspace\11058221\products' | Where-Object { $_.Name -ne 'requests.jsonl' } | ForEach-Object {
    $html = Join-Path $_.FullName 'product.html'
    $size = if (Test-Path $html) { (Get-Item $html).Length } else { 0 }
    [PSCustomObject]@{
        ASIN = $_.Name
        SizeKB = [math]::Round($size/1KB, 1)
        HasHTML = (Test-Path $html)
    }
} | Sort-Object SizeKB -Descending | Select-Object -First 15 | Format-Table -AutoSize