# ==========================================================
# 휴대폰과 연결(Phone Link) 문자 자동발송 (integrations/phone_link_sender.py)
# ----------------------------------------------------------
# 주의: 이건 마이크로소프트의 공식 API가 아닙니다. Windows "휴대폰과 연결"
# 프로그램의 화면을 자동으로 조작(클릭/입력)해서 문자를 보내는 방식입니다
# (UI 자동화). 실제 UI 요소(AutomationId)를 화면에서 직접 확인해서 만들었지만,
# Phone Link가 업데이트되면 화면 구성이 바뀌어 갑자기 동작하지 않을 수 있습니다.
#
# 안전장치:
#   1. 시도 전에 항상 문구를 클립보드에 먼저 복사합니다 - 자동화가 실패해도
#      직접 붙여넣기만 하면 되도록.
#   2. Phone Link가 켜져있는지, "연결됨" 상태인지 먼저 확인하고, 아니면
#      자동화를 아예 시도하지 않습니다.
#   3. 자동화 각 단계(새 메시지 버튼, 받는사람 입력칸, 메시지 입력칸, 전송
#      버튼)에서 예상한 화면 요소를 못 찾으면 그 즉시 멈추고 실패로 보고합니다.
#      (엉뚱한 곳을 눌러버리지 않도록)
#   4. 정말로 "전송" 버튼을 눌렀는지까지만 확인 가능하고, 문자가 실제로
#      통신사를 통해 도착했는지는 이 프로그램이 확인할 방법이 없습니다.
# ==========================================================

import base64
import json
import subprocess
import tempfile
import os


class PhoneLinkError(Exception):
    """자동발송이 실패했을 때 발생합니다. .status로 원인을 구분할 수 있습니다."""

    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(message)


def _copy_to_clipboard(text: str) -> None:
    """실패해도 사용자가 직접 붙여넣을 수 있도록, 시도 전에 항상 클립보드에 복사해둡니다."""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
        input=text,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )


# PowerShell 5.1 자동화 스크립트입니다. 실제로 Phone Link 창을 열어서
# UI Automation으로 화면 요소를 하나씩 찾아 클릭/입력합니다.
# 전화번호/문구는 명령줄 인자로 넘기면 이스케이프 문제가 생길 수 있어서,
# 환경변수(PL_PAYLOAD_PATH)로 임시 JSON 파일 경로만 넘기고, 실제 내용은
# 그 파일에서 읽습니다.
_PS_SCRIPT = r"""
$ErrorActionPreference = "Stop"
$payloadPath = $env:PL_PAYLOAD_PATH
$resultPath = "$payloadPath.result"

function Out-Result($status, $message) {
    $obj = @{ status = $status; message = $message }
    ($obj | ConvertTo-Json -Compress) | Set-Content -Path $resultPath -Encoding UTF8
}

try {
    $payload = Get-Content -Path $payloadPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $phone = $payload.phone
    $body = $payload.message

    Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class PLWin32 {
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Windows.Forms

    $global:plHwnd = [IntPtr]::Zero
    $cb = {
        param($hWnd, $lParam)
        if ([PLWin32]::IsWindowVisible($hWnd)) {
            $sb = New-Object System.Text.StringBuilder 256
            [PLWin32]::GetWindowText($hWnd, $sb, 256) | Out-Null
            if ($sb.ToString() -like "*휴대폰과 연결*") { $global:plHwnd = $hWnd }
        }
        return $true
    }
    [PLWin32]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null

    if ($global:plHwnd -eq [IntPtr]::Zero) {
        Out-Result "not_ready" "Phone Link(휴대폰과 연결) 프로그램이 켜져있지 않습니다."
        exit
    }

    [PLWin32]::SetForegroundWindow($global:plHwnd) | Out-Null
    Start-Sleep -Milliseconds 400

    $win = [System.Windows.Automation.AutomationElement]::FromHandle($global:plHwnd)

    $connCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, "ConnectivityStatusTextBlock")
    $connEl = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $connCond)
    if ($null -eq $connEl -or $connEl.Current.Name -ne "연결됨") {
        Out-Result "not_ready" "휴대폰이 Phone Link에 연결되어 있지 않습니다."
        exit
    }

    $newBtnCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, "NewMessageButton")
    $newBtn = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $newBtnCond)
    if ($null -eq $newBtn) { Out-Result "automation_failed" "'새 메시지' 버튼을 찾을 수 없습니다 (화면 구성이 바뀌었을 수 있습니다)."; exit }
    $newBtn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    Start-Sleep -Milliseconds 700

    $contactBoxCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, "ContactSuggestionsBox")
    $contactBox = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $contactBoxCond)
    if ($null -eq $contactBox) { Out-Result "automation_failed" "받는사람 입력칸을 찾을 수 없습니다."; exit }
    $editCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)
    $editBox = $contactBox.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $editCond)
    if ($null -eq $editBox) { Out-Result "automation_failed" "받는사람 입력창을 찾을 수 없습니다."; exit }
    $editBox.SetFocus()
    Start-Sleep -Milliseconds 200
    $editBox.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($phone)
    Start-Sleep -Milliseconds 300
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    Start-Sleep -Milliseconds 700

    $inputCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, "InputTextBox")
    $inputBox = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $inputCond)
    if ($null -eq $inputBox) { Out-Result "automation_failed" "메시지 입력창을 찾을 수 없습니다 (받는사람이 정상적으로 확정되지 않았을 수 있습니다)."; exit }
    $inputBox.SetFocus()
    Start-Sleep -Milliseconds 200
    $inputBox.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($body)
    Start-Sleep -Milliseconds 400

    $sendCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, "SendMessageButton")
    $sendBtn = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $sendCond)
    if ($null -eq $sendBtn) { Out-Result "automation_failed" "전송 버튼을 찾을 수 없습니다."; exit }
    if (-not $sendBtn.Current.IsEnabled) { Out-Result "automation_failed" "전송 버튼이 비활성화 상태입니다 (받는사람/문구를 다시 확인해주세요)."; exit }

    $sendBtn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    Start-Sleep -Milliseconds 500

    Out-Result "sent" "전송 버튼을 눌렀습니다."
} catch {
    Out-Result "error" "자동화 중 오류가 발생했습니다: $($_.Exception.Message)"
}
"""


def send_message(phone: str, message: str) -> None:
    """
    Phone Link를 자동조작해서 문자를 보냅니다. 실패하면 PhoneLinkError를 냅니다.
    성공/실패와 무관하게, 시도 전에 문구를 항상 클립보드에 복사해둡니다.
    """
    _copy_to_clipboard(message)

    payload_fd, payload_path = tempfile.mkstemp(suffix=".json")
    result_path = payload_path + ".result"
    try:
        with os.fdopen(payload_fd, "w", encoding="utf-8") as f:
            json.dump({"phone": phone, "message": message}, f, ensure_ascii=False)

        encoded_script = base64.b64encode(_PS_SCRIPT.encode("utf-16-le")).decode("ascii")

        env = dict(os.environ)
        env["PL_PAYLOAD_PATH"] = payload_path

        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_script],
            env=env,
            capture_output=True,
            timeout=30,
        )

        if not os.path.exists(result_path):
            raise PhoneLinkError("error", "자동화 결과를 확인할 수 없습니다 (PowerShell 실행 자체가 실패했을 수 있습니다).")

        # Windows PowerShell 5.1의 Set-Content -Encoding UTF8은 파일 맨 앞에 BOM이라는
        # 눈에 안 보이는 표시를 붙입니다. 그냥 "utf-8"로 읽으면 그 BOM 때문에 JSON
        # 해석이 실패하므로, BOM을 자동으로 걸러주는 "utf-8-sig"로 읽습니다.
        with open(result_path, "r", encoding="utf-8-sig") as f:
            result = json.load(f)

        if result["status"] != "sent":
            raise PhoneLinkError(result["status"], result["message"])
    finally:
        for path in (payload_path, result_path):
            if os.path.exists(path):
                os.remove(path)
