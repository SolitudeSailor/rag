param([string]$InputDocx, [string]$OutputDir)
$ErrorActionPreference='Stop'
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
Add-Type -AssemblyName System.Drawing
$word=$null; $doc=$null
try {
    $word=New-Object -ComObject Word.Application
    $word.Visible=$false; $word.DisplayAlerts=0; $word.AutomationSecurity=3
    $doc=$word.Documents.Open($InputDocx,$false,$true)
    Write-Output 'Opened'
    $word.ActiveWindow.View.Type=3
    $doc.Repaginate()
    Write-Output 'Repaginated'
    $pages=$word.ActiveWindow.Panes.Item(1).Pages
    Write-Output ('Pages='+$pages.Count)
    for ($i=1; $i -le $pages.Count; $i++) {
        [byte[]]$bits=$pages.Item($i).EnhMetaFileBits
        $stream=New-Object System.IO.MemoryStream(,$bits)
        $image=[System.Drawing.Image]::FromStream($stream)
        $bitmap=New-Object System.Drawing.Bitmap(1240,1754)
        $graphics=[System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.Clear([System.Drawing.Color]::White)
        $graphics.DrawImage($image,0,0,1240,1754)
        $bitmap.Save((Join-Path $OutputDir ('page-'+$i+'.png')),[System.Drawing.Imaging.ImageFormat]::Png)
        $graphics.Dispose(); $bitmap.Dispose(); $image.Dispose(); $stream.Dispose()
        Write-Output ('Page '+$i+' rendered')
    }
} finally {
    if ($doc) { $doc.Close(0) }
    if ($word) { $word.Quit() }
}
