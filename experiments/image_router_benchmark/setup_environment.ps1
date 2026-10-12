param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "../..")).Path
$experimentWork = Join-Path $projectRoot ".work/image-router-benchmark"
$venvRoot = Join-Path $experimentWork "venv"
$venvPython = Join-Path $venvRoot "Scripts/python.exe"
$vendorRoot = Join-Path $experimentWork "vendor"
$mobileClipRoot = Join-Path $vendorRoot "ml-mobileclip"
$mobileClipRevision = "aecfb5453d022e9deff12f81a150ea8f35194baa"

New-Item -ItemType Directory -Force -Path $experimentWork | Out-Null
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv --system-site-packages $venvRoot
}

& $venvPython -m pip install --disable-pip-version-check --index-url https://pypi.org/simple `
    -r (Join-Path $PSScriptRoot "requirements-experiment.txt")

New-Item -ItemType Directory -Force -Path $vendorRoot | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $mobileClipRoot ".git"))) {
    git clone https://github.com/apple/ml-mobileclip.git $mobileClipRoot
}
git -C $mobileClipRoot cat-file -e "$mobileClipRevision^{commit}" 2>$null
if ($LASTEXITCODE -ne 0) {
    git -C $mobileClipRoot fetch origin $mobileClipRevision
}
git -C $mobileClipRoot checkout --detach $mobileClipRevision
& $venvPython -m pip install --disable-pip-version-check --no-deps `
    --no-build-isolation --editable $mobileClipRoot

Write-Output "Experiment Python: $venvPython"
Write-Output "Model cache: $(Join-Path $experimentWork 'models')"
