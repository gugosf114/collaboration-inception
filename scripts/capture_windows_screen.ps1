param(
    [string]$OutputPath,
    [switch]$AsJson
)

$ErrorActionPreference = "Stop"
if (-not $AsJson -and [string]::IsNullOrWhiteSpace($OutputPath)) {
    throw "Use -OutputPath FILE or -AsJson."
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen(
        $bounds.Left,
        $bounds.Top,
        0,
        0,
        $bitmap.Size,
        [System.Drawing.CopyPixelOperation]::SourceCopy
    )
    if ($AsJson) {
        $stream = New-Object System.IO.MemoryStream
        try {
            $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
            [pscustomobject]@{
                pngBase64 = [Convert]::ToBase64String($stream.ToArray())
                bounds = [pscustomobject]@{
                    x = $bounds.Left
                    y = $bounds.Top
                    width = $bounds.Width
                    height = $bounds.Height
                }
            } | ConvertTo-Json -Compress
        }
        finally {
            $stream.Dispose()
        }
    }
    else {
        $parent = Split-Path -Parent $OutputPath
        if ($parent) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        $bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
    }
}
finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
