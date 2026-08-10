// 실행 파일만 등록할 수 있는 환경(DuoNX)을 위한 껍데기.
//
// PyInstaller처럼 파이썬을 통째로 묶지 않는다. 그냥 옆에 있는 venv의
// python.exe 를 인수와 함께 부른다. 그것이 우리가 못 주는 인수다.
//
// 이 방법을 택한 이유:
//   - 파이썬을 묶지 않으니 인터프리터가 시작을 못 하는 일이 없다
//     (PyInstaller로는 'No module named encodings' 에서 끝내 못 벗어났다).
//   - Windows에 원래 들어 있는 C# 컴파일러로 만든다. 설치할 것이 없다.
//   - 몇 KB짜리라 백신 오탐도 덜하다.
//
// 전제: 이 exe 옆에 venv 와 src 가 그대로 있어야 한다. 즉 프로젝트 폴더에
// 두고 쓴다. 그 PC에서 run.bat 이 되고 있다면 이것도 된다.

using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;

class Launcher
{
    static int Main()
    {
        string here = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string python = Path.Combine(here, "venv", "Scripts", "python.exe");

        if (!File.Exists(python))
        {
            Console.Error.WriteLine("venv를 찾지 못했습니다: " + python);
            Console.Error.WriteLine("이 파일을 프로젝트 폴더에 두고, setup.bat 을 먼저 실행해 주세요.");
            Console.Error.Write("Enter를 누르면 닫힙니다 ");
            Console.ReadLine();
            return 1;
        }

        // 시작 위치를 여기로 못 박는다. DuoNX는 시작 위치를 주지 못하고,
        // 그러면 settings.json 이 엉뚱한 폴더에 생긴다.
        ProcessStartInfo run = new ProcessStartInfo(python, "-m src.gui");
        run.WorkingDirectory = here;
        run.UseShellExecute = false;

        Process child = Process.Start(run);
        child.WaitForExit();
        return child.ExitCode;
    }
}
