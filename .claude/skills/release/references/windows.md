# release — 5단계: Windows VM에서 배포 zip 확인

VM 구성과 `scripts/utm-windows-ps.sh`의 동작은 `docs/windows-vm-testing.md`에 있다.
1.1.18, 1.2.0, 1.2.1 릴리스에서 같은 절차를 밟았다.

## 준비

- VM을 켤지는 본문 5단계를 따른다. 여기서 `utmctl start Windows`는 2026-11-03에 AppleEvent 오류
  -1712로, 2026-11-27에 시간 초과(124)로 끝났다. 사용자에게 UTM에서 켜 달라고 한다.
- 저장소가 공개라 릴리스 파일은 VM 안에서 바로 받는다. Mac에서 받아 push하지 않는다.
- 확인 폴더는 매번 새로 만든다(`release-X.Y.Z-verify`). 이미 있으면 멈춘다.

## 검사·실행 스크립트

`scripts/utm-windows-ps.sh --user 500 <<'EOF'`로 보낸다. `$ver`만 바꾼다.

```powershell
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'
$ver = '1.2.1'
$root = "$env:USERPROFILE\release-$ver-verify"
if (Test-Path $root) { throw "$root exists" }
New-Item -ItemType Directory "$root\downloads" | Out-Null
$base = "https://github.com/ChunSam/tokki/releases/download/v$ver"
foreach ($rid in 'win-arm64','win-x64') {
  $zip = "Tokki-Windows-v$ver-$rid.zip"
  Invoke-WebRequest -UseBasicParsing "$base/$zip" -OutFile "$root\downloads\$zip"
  Invoke-WebRequest -UseBasicParsing "$base/$zip.sha256" -OutFile "$root\downloads\$zip.sha256"
  $bytes = [IO.File]::ReadAllBytes("$root\downloads\$zip.sha256"); $parts = ([Text.Encoding]::ASCII.GetString($bytes)).Trim() -split '\s+'
  $ok = ($parts[1] -eq $zip) -and ($parts[0].ToLower() -eq (Get-FileHash -Algorithm SHA256 "$root\downloads\$zip").Hash.ToLower()) -and ($bytes[-1] -eq 10) -and -not ($bytes -contains 13)
  Expand-Archive "$root\downloads\$zip" "$root\$rid"
  $exeBytes = [IO.File]::ReadAllBytes("$root\$rid\TokenTracker.Windows.exe"); $pe = [BitConverter]::ToInt32($exeBytes, 0x3C)
  $v = (Get-Item "$root\$rid\TokenTracker.Windows.exe").VersionInfo
  "[$rid] checksum ok=$ok  file=$($v.FileVersion) product=$($v.ProductVersion)  PE=0x{0:X4}" -f [BitConverter]::ToUInt16($exeBytes, $pe + 4)
}
$ErrorActionPreference = 'Continue'
$exe = "$root\win-arm64\TokenTracker.Windows.exe"
Get-Process -Name TokenTracker.Windows -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force; $_.WaitForExit(10000) | Out-Null }
Start-Sleep 2
$start = Get-Date
Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe)
Start-Sleep 8
Get-ChildItem 'HKCU:\Control Panel\NotifyIconSettings' | Where-Object { (Get-ItemProperty $_.PSPath).ExecutablePath -eq $exe } | ForEach-Object { Set-ItemProperty $_.PSPath -Name IsPromoted -Value 1 -Type DWord }
$c = Get-Content "$env:APPDATA\Token Tracker\usage-cache.json" -Raw | ConvertFrom-Json
"started $($start.ToString('HH:mm:ss'))  claude=$($c.Snapshot.Claude.Source) $($c.Snapshot.Claude.RemainingPercent5h)%  updated=$(([DateTimeOffset]$c.Snapshot.UpdatedAt).ToLocalTime().ToString('HH:mm:ss'))"
Get-WinEvent -FilterHashtable @{LogName='Application'; Level=1,2; StartTime=$start} -ErrorAction SilentlyContinue | ForEach-Object { "  EVENT $($_.ProviderName): $($_.Message.Substring(0, [Math]::Min(200, $_.Message.Length)))" }
```

## 설치본 교체

위 검사가 통과하면 VM의 설치본(`C:\Users\vw\TokenTracker`)을 같은 판으로 바꾼다. 확인 폴더에서만
실행하고 끝내면 설치본이 옛 판으로 남는다. 1.1.17이 1.4.2 릴리스 뒤까지 남아 있다가 `status` 점검에서야
드러났다(2026-11-20). 같은 경로에 넣으면 트레이 아이콘의 `IsPromoted` 설정도 그대로 이어진다.

```powershell
$ErrorActionPreference = 'Stop'
$ver = '1.2.1'
$dest = "$env:USERPROFILE\TokenTracker"; $old = "$dest-previous"
if (Test-Path $old) { throw "$old exists" }
Get-Process -Name TokenTracker.Windows -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force; $_.WaitForExit(10000) | Out-Null }
Rename-Item $dest $old
Copy-Item -Recurse "$env:USERPROFILE\release-$ver-verify\win-arm64" $dest
Start-Process -FilePath "$dest\TokenTracker.Windows.exe" -WorkingDirectory $dest
Start-Sleep 10
"version=$((Get-Item "$dest\TokenTracker.Windows.exe").VersionInfo.ProductVersion) running=$([bool](Get-Process -Name TokenTracker.Windows -ErrorAction SilentlyContinue))"
```

`usage-cache.json`의 `updated`가 실행 뒤로 바뀐 것을 확인한 다음 `$old`를 지운다. 컴퓨터를 켤 때 자동 실행은
VM에서 꺼 두었으므로 Run 키는 건드리지 않는다.

## 통과 기준

- 두 zip 모두 `checksum ok=True`. 체크섬 파일은 파일명만 담고 LF로 끝나야 한다(CR이 없어야 한다).
- `file`은 `X.Y.Z.<build>`, `product`는 `X.Y.Z+<태그 커밋의 전체 SHA>`다(1.2.1: `1.2.1.22`,
  `1.2.1+c5c4f137…`). `1.0.0`이면 publish의 버전 스탬프가 빠진 것이다.
- PE는 arm64가 `0xAA64`, x64가 `0x8664`다.
- `claude=Api`이고 `updated`가 `started` 뒤다. `EVENT` 줄이 없어야 한다(앱 크래시는 여기 찍힌다).

## Gotchas

- 트레이 아이콘은 exe 경로별로 기억된다. 새 폴더에서 실행하면 숨김 영역에 들어가므로 위
  스크립트의 `IsPromoted=1`이 필요하다. 그래야 UIA로 `Shell_TrayWnd` 아래 Button으로 잡힌다.
- 바뀐 메뉴를 확인할 때는 `mouse_event`로 아이콘을 우클릭하고, 열린 메뉴를 최상위 `Menu`
  요소로 읽는다. UIA Button의 Name은 "첫 툴팁 + 현재 툴팁"이 붙은 값이라 화면의 툴팁이 아니다.
- 메뉴 항목도 `mouse_event`로 클릭한다. UIA `InvokePattern`으로 연 설정창은 컨트롤이 그려지지
  않았다. 창을 다루는 다른 함정은 `docs/windows-vm-testing.md`의 "Driving the app's windows"에 있다.
- 끝나면 확인 폴더는 남겨 두고, 지울지 사용자에게 묻는다.
