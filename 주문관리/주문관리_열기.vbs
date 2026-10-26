' ============================================================
'  주문관리 열기 (검은 명령창 없이 실행)
'  - 서버(localhost:8501)가 꺼져 있으면 '숨김'으로 조용히 켠 뒤,
'  - 크롬 앱 창으로 주문관리 화면을 엽니다.
'  기존 '주문관리_실행.bat'과 달리 검은 cmd 창이 뜨지 않습니다.
'  (이 파일을 더블클릭하거나 작업표시줄에 고정해서 쓰세요)
' ============================================================

Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = appDir

' --- 서버가 이미 떠 있는지 health로 확인 ---
Function ServerUp()
    ServerUp = False
    On Error Resume Next
    Dim http : Set http = CreateObject("MSXML2.ServerXMLHTTP")
    http.setTimeouts 1000, 1000, 2000, 2000
    http.Open "GET", "http://localhost:8501/_stcore/health", False
    http.Send
    If Err.Number = 0 And http.status = 200 Then ServerUp = True
    On Error GoTo 0
End Function

' --- 서버가 없으면 숨김(창 없이)으로 시작하고, 준비될 때까지 대기 ---
If Not ServerUp() Then
    sh.Run "cmd /c .venv\Scripts\streamlit.exe run app.py --server.headless=true --server.port=8501", 0, False
    Dim i
    For i = 1 To 120   ' 최대 약 60초 대기
        WScript.Sleep 500
        If ServerUp() Then Exit For
    Next
End If

' --- 크롬 앱 창으로 화면 열기 (크롬 없으면 엣지, 그것도 없으면 기본 브라우저) ---
Dim la, chrome, edge
la = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")
chrome = ""
Dim candidates
candidates = Array( _
    sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Google\Chrome\Application\chrome.exe", _
    sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Google\Chrome\Application\chrome.exe", _
    la & "\Google\Chrome\Application\chrome.exe")
Dim c
For Each c In candidates
    If chrome = "" And fso.FileExists(c) Then chrome = c
Next

If chrome <> "" Then
    sh.Run """" & chrome & """ --app=http://localhost:8501 --lang=ko-KR --user-data-dir=""" & la & "\OrderManagementApp_Chrome"" --window-size=1600,950", 1, False
Else
    edge = sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Microsoft\Edge\Application\msedge.exe"
    If fso.FileExists(edge) Then
        sh.Run """" & edge & """ --app=http://localhost:8501 --lang=ko-KR --user-data-dir=""" & la & "\OrderManagementApp"" --window-size=1600,950", 1, False
    Else
        sh.Run "http://localhost:8501", 1, False
    End If
End If
