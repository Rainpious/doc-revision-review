param([Parameter(Mandatory=$true)][string]$InputDocx,
      [Parameter(Mandatory=$true)][string]$OutputPdf)
$ErrorActionPreference = 'Stop'
$inputFile = (Resolve-Path -LiteralPath $InputDocx).Path
$outputFile = [IO.Path]::GetFullPath($OutputPdf)
if ([IO.Path]::GetExtension($inputFile) -ne '.docx') { throw 'Input must be DOCX' }
if (Test-Path -LiteralPath $outputFile) { throw 'Output exists; choose a new path' }
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($outputFile)) | Out-Null
$wordApp = $null
$reportDoc = $null
try {
    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordApp.AutomationSecurity = 3
    $reportDoc = $wordApp.Documents.Open($inputFile, $false, $true, $false)
    # Fixed format export; do not update fields, accept revisions or save source DOCX.
    $reportDoc.ExportAsFixedFormat($outputFile, 17)
    Write-Output $outputFile
} finally {
    $saveChanges = 0
    try {
        if ($reportDoc) { $reportDoc.Close([ref]$saveChanges); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($reportDoc) }
    } finally {
        if ($wordApp) { $wordApp.Quit([ref]$saveChanges); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wordApp) }
    }
}
