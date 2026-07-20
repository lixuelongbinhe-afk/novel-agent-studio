using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace NovelAgentStudioUninstaller
{
    internal static class Program
    {
        private const string AppFolder = "NovelAgentStudio";
        private const string DisplayName = "Novel Agent Studio";
        private const int MoveFileDelayUntilReboot = 0x4;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool MoveFileEx(string existing, string replacement, int flags);

        [STAThread]
        private static int Main(string[] args)
        {
            string marker = CleanupMarkerPath();
            try
            {
                if (args.Length >= 4 && args[0] == "--cleanup")
                {
                    Cleanup(args[1], args[2] == "1", Int32.Parse(args[3]));
                    return 0;
                }
                bool silent = Array.IndexOf(args, "--silent") >= 0;
                bool deleteData = false;
                if (!silent)
                {
                    DialogResult answer = MessageBox.Show(
                        "是否卸载 Novel Agent Studio？\n\n选择“是”将保留小说数据库和本地设置。",
                        "卸载 Novel Agent Studio",
                        MessageBoxButtons.YesNo,
                        MessageBoxIcon.Question
                    );
                    if (answer != DialogResult.Yes) return 0;
                    DialogResult dataAnswer = MessageBox.Show(
                        "是否同时删除本地小说数据库、日志和设置？\n\n此操作不可恢复，建议先创建完整备份。",
                        "用户数据",
                        MessageBoxButtons.YesNoCancel,
                        MessageBoxIcon.Warning,
                        MessageBoxDefaultButton.Button2
                    );
                    if (dataAnswer == DialogResult.Cancel) return 0;
                    deleteData = dataAnswer == DialogResult.Yes;
                }
                EnsureNotRunning();
                File.WriteAllText(marker, DateTime.UtcNow.ToString("O"), Encoding.ASCII);
                RemoveShortcutsAndRegistry();
                string target = ExpectedInstallDirectory();
                string temporary = Path.Combine(Path.GetTempPath(), "NAS-uninstall-" + Guid.NewGuid().ToString("N") + ".exe");
                File.Copy(Application.ExecutablePath, temporary, true);
                ProcessStartInfo info = new ProcessStartInfo(
                    temporary,
                    "--cleanup \"" + target + "\" " + (deleteData ? "1" : "0") + " " + Process.GetCurrentProcess().Id
                );
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                Process.Start(info);
                return 0;
            }
            catch (Exception ex)
            {
                if (args.Length == 0 || args[0] != "--cleanup") TryDelete(marker);
                File.WriteAllText(Path.Combine(Path.GetTempPath(), "NovelAgentStudio-uninstall-error.log"), ex.ToString(), Encoding.UTF8);
                if (Array.IndexOf(args, "--silent") < 0)
                    MessageBox.Show(ex.Message, "卸载失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }

        private static void Cleanup(string target, bool deleteData, int parentPid)
        {
            try
            {
                try
                {
                    Process parent = Process.GetProcessById(parentPid);
                    parent.WaitForExit(10000);
                }
                catch (ArgumentException) { }
                string expected = ExpectedInstallDirectory();
                if (!String.Equals(Path.GetFullPath(target), expected, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("拒绝删除非预期安装目录。 ");
                for (int attempt = 0; attempt < 20 && Directory.Exists(expected); attempt++)
                {
                    try { Directory.Delete(expected, true); }
                    catch (IOException) { Thread.Sleep(250); }
                    catch (UnauthorizedAccessException) { Thread.Sleep(250); }
                }
                if (Directory.Exists(expected)) throw new IOException("程序目录仍被占用，请重启后重试。 ");
                if (deleteData)
                {
                    string data = ExpectedDataDirectory();
                    if (Directory.Exists(data)) Directory.Delete(data, true);
                }
            }
            finally
            {
                TryDelete(CleanupMarkerPath());
                MoveFileEx(Application.ExecutablePath, null, MoveFileDelayUntilReboot);
            }
        }

        private static string CleanupMarkerPath()
        {
            return Path.Combine(Path.GetTempPath(), "NovelAgentStudio-uninstalling.lock");
        }

        private static void TryDelete(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        private static void EnsureNotRunning()
        {
            if (Process.GetProcessesByName("NovelAgentStudio").Length > 0 ||
                Process.GetProcessesByName("NovelAgentStudioConsole").Length > 0)
                throw new InvalidOperationException("请先关闭正在运行的 Novel Agent Studio。 ");
        }

        private static void RemoveShortcutsAndRegistry()
        {
            string startFolder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), DisplayName);
            if (Directory.Exists(startFolder)) Directory.Delete(startFolder, true);
            string desktop = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), DisplayName + ".lnk");
            if (File.Exists(desktop)) File.Delete(desktop);
            Registry.CurrentUser.DeleteSubKeyTree(
                @"Software\Microsoft\Windows\CurrentVersion\Uninstall\NovelAgentStudio",
                false
            );
        }

        private static string ExpectedInstallDirectory()
        {
            return Path.GetFullPath(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs",
                AppFolder
            ));
        }

        private static string ExpectedDataDirectory()
        {
            return Path.GetFullPath(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                AppFolder
            ));
        }
    }
}
