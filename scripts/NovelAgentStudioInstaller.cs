using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Novel Agent Studio Installer")]
[assembly: AssemblyDescription("Novel Agent Studio Windows Installer")]
[assembly: AssemblyCompany("Novel Agent Studio")]
[assembly: AssemblyProduct("Novel Agent Studio Installer")]
[assembly: AssemblyVersion("2.2.4.0")]
[assembly: AssemblyFileVersion("2.2.4.0")]

namespace NovelAgentStudioInstaller
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            if (Array.IndexOf(args, "--silent") >= 0)
            {
                try
                {
                    InstallerEngine.Install(false, false, null);
                    return 0;
                }
                catch (Exception ex)
                {
                    InstallerEngine.WriteError(ex);
                    return 1;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new InstallerForm());
            return Environment.ExitCode;
        }
    }

    internal sealed class InstallerForm : Form
    {
        private readonly CheckBox desktopShortcut;
        private readonly CheckBox launchAfterInstall;
        private readonly Button installButton;
        private readonly Button cancelButton;
        private readonly ProgressBar progress;
        private readonly Label status;

        internal InstallerForm()
        {
            Text = "Novel Agent Studio 安装程序";
            ClientSize = new Size(590, 360);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(246, 248, 247);
            Font = new Font("Microsoft YaHei UI", 9F);

            Label title = new Label();
            title.Text = "小说智能体工作室";
            title.Font = new Font("Microsoft YaHei UI", 20F, FontStyle.Bold);
            title.ForeColor = Color.FromArgb(23, 32, 30);
            title.AutoSize = true;
            title.Location = new Point(34, 28);
            Controls.Add(title);

            Label version = new Label();
            version.Text = "Novel Agent Studio  v2.2.4";
            version.ForeColor = Color.FromArgb(80, 103, 98);
            version.AutoSize = true;
            version.Location = new Point(37, 73);
            Controls.Add(version);

            Label pathLabel = new Label();
            pathLabel.Text = "安装位置";
            pathLabel.AutoSize = true;
            pathLabel.Location = new Point(37, 116);
            Controls.Add(pathLabel);

            TextBox path = new TextBox();
            path.ReadOnly = true;
            path.Text = InstallerEngine.InstallDirectory;
            path.Location = new Point(40, 139);
            path.Size = new Size(510, 27);
            Controls.Add(path);

            desktopShortcut = new CheckBox();
            desktopShortcut.Text = "创建桌面快捷方式";
            desktopShortcut.Checked = true;
            desktopShortcut.AutoSize = true;
            desktopShortcut.Location = new Point(40, 190);
            Controls.Add(desktopShortcut);

            launchAfterInstall = new CheckBox();
            launchAfterInstall.Text = "安装完成后启动";
            launchAfterInstall.Checked = true;
            launchAfterInstall.AutoSize = true;
            launchAfterInstall.Location = new Point(244, 190);
            Controls.Add(launchAfterInstall);

            status = new Label();
            status.Text = "将安装当前用户版本，不需要管理员权限。";
            status.ForeColor = Color.FromArgb(92, 107, 103);
            status.AutoEllipsis = true;
            status.Location = new Point(40, 229);
            status.Size = new Size(510, 22);
            Controls.Add(status);

            progress = new ProgressBar();
            progress.Location = new Point(40, 254);
            progress.Size = new Size(510, 12);
            progress.Style = ProgressBarStyle.Blocks;
            Controls.Add(progress);

            installButton = new Button();
            installButton.Text = "安装";
            installButton.Size = new Size(116, 38);
            installButton.Location = new Point(434, 294);
            installButton.BackColor = Color.FromArgb(23, 107, 102);
            installButton.ForeColor = Color.White;
            installButton.FlatStyle = FlatStyle.Flat;
            installButton.FlatAppearance.BorderSize = 0;
            installButton.Click += InstallClicked;
            Controls.Add(installButton);

            cancelButton = new Button();
            cancelButton.Text = "取消";
            cancelButton.Size = new Size(92, 38);
            cancelButton.Location = new Point(330, 294);
            cancelButton.Click += delegate { Close(); };
            Controls.Add(cancelButton);
            AcceptButton = installButton;
            CancelButton = cancelButton;
        }

        private void InstallClicked(object sender, EventArgs args)
        {
            installButton.Enabled = false;
            cancelButton.Enabled = false;
            desktopShortcut.Enabled = false;
            launchAfterInstall.Enabled = false;
            progress.Style = ProgressBarStyle.Marquee;
            status.Text = "正在校验安装文件...";
            TaskScheduler ui = TaskScheduler.FromCurrentSynchronizationContext();
            Task.Factory.StartNew(delegate
            {
                InstallerEngine.Install(
                    desktopShortcut.Checked,
                    launchAfterInstall.Checked,
                    delegate(string message)
                    {
                        BeginInvoke((MethodInvoker)delegate { status.Text = message; });
                    }
                );
            }).ContinueWith(delegate(Task task)
            {
                progress.Style = ProgressBarStyle.Blocks;
                if (task.IsFaulted)
                {
                    Exception error = task.Exception == null ? new Exception("安装失败") : task.Exception.GetBaseException();
                    InstallerEngine.WriteError(error);
                    MessageBox.Show(this, error.Message, "安装失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    installButton.Enabled = true;
                    cancelButton.Enabled = true;
                    desktopShortcut.Enabled = true;
                    launchAfterInstall.Enabled = true;
                    status.Text = "安装未完成。";
                    Environment.ExitCode = 1;
                    return;
                }
                status.Text = "安装完成。";
                MessageBox.Show(this, "Novel Agent Studio 已安装完成。", "安装完成", MessageBoxButtons.OK, MessageBoxIcon.Information);
                Close();
            }, ui);
        }
    }

    internal static class InstallerEngine
    {
        internal const string AppFolder = "NovelAgentStudio";
        internal const string DisplayName = "Novel Agent Studio";
        internal const string Version = "2.2.4";
        internal static readonly string InstallDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs",
            AppFolder
        );

        internal static void Install(bool desktopShortcut, bool launch, Action<string> progress)
        {
            WaitForPendingUninstall();
            EnsureNotRunning();
            string staging = Path.Combine(Path.GetTempPath(), "NAS-install-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(staging);
            try
            {
                Report(progress, "正在校验安装文件...");
                string payload = ExtractEmbeddedPayloadToTemp();
                try
                {
                    Report(progress, "正在解压应用文件...");
                    ExtractZip(payload, staging);
                }
                finally
                {
                    File.Delete(payload);
                }
                RequirePayload(staging);
                Report(progress, "正在更新程序目录...");
                ReplaceInstallDirectory(staging);
                Report(progress, "正在创建快捷方式...");
                CreateShortcuts(desktopShortcut);
                RegisterUninstaller();
                if (launch)
                {
                    ProcessStartInfo info = new ProcessStartInfo(Path.Combine(InstallDirectory, "NovelAgentStudio.exe"));
                    info.WorkingDirectory = InstallDirectory;
                    info.UseShellExecute = true;
                    Process.Start(info);
                }
            }
            finally
            {
                if (Directory.Exists(staging))
                {
                    Directory.Delete(staging, true);
                }
            }
        }

        private static void Report(Action<string> progress, string message)
        {
            if (progress != null) progress(message);
        }

        private static void EnsureNotRunning()
        {
            if (Process.GetProcessesByName("NovelAgentStudio").Length > 0 ||
                Process.GetProcessesByName("NovelAgentStudioConsole").Length > 0)
            {
                throw new InvalidOperationException("请先关闭正在运行的 Novel Agent Studio。 ");
            }
        }

        private static void WaitForPendingUninstall()
        {
            string marker = Path.Combine(Path.GetTempPath(), "NovelAgentStudio-uninstalling.lock");
            DateTime deadline = DateTime.UtcNow.AddSeconds(60);
            while (File.Exists(marker))
            {
                try
                {
                    if (DateTime.UtcNow - File.GetLastWriteTimeUtc(marker) > TimeSpan.FromMinutes(10))
                    {
                        File.Delete(marker);
                        return;
                    }
                }
                catch (FileNotFoundException)
                {
                    return;
                }
                if (DateTime.UtcNow >= deadline)
                    throw new IOException("上一次卸载仍在清理文件，请稍后重新运行安装程序。 ");
                Thread.Sleep(250);
            }
        }

        private static string ExtractEmbeddedPayloadToTemp()
        {
            Assembly assembly = Assembly.GetExecutingAssembly();
            string expected;
            using (Stream checksum = assembly.GetManifestResourceStream("payload.sha256"))
            {
                if (checksum == null) throw new InvalidOperationException("安装包缺少 payload 校验值。 ");
                using (StreamReader reader = new StreamReader(checksum, Encoding.ASCII))
                {
                    expected = reader.ReadToEnd().Trim().ToLowerInvariant();
                }
            }
            string temp = Path.Combine(Path.GetTempPath(), "NAS-payload-" + Guid.NewGuid().ToString("N") + ".zip");
            using (Stream source = assembly.GetManifestResourceStream("payload.zip"))
            {
                if (source == null) throw new InvalidOperationException("安装包缺少应用 payload。 ");
                using (FileStream target = File.Create(temp)) source.CopyTo(target);
            }
            string actual;
            using (SHA256 sha = SHA256.Create())
            using (FileStream input = File.OpenRead(temp))
            {
                actual = BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
            }
            if (!String.Equals(expected, actual, StringComparison.Ordinal))
            {
                File.Delete(temp);
                throw new InvalidDataException("安装文件 SHA-256 校验失败。 ");
            }
            return temp;
        }

        private static void ExtractZip(string payload, string staging)
        {
            string root = Path.GetFullPath(staging + Path.DirectorySeparatorChar);
            long total = 0;
            using (ZipArchive archive = ZipFile.OpenRead(payload))
            {
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    total += entry.Length;
                    if (total > 2L * 1024L * 1024L * 1024L)
                        throw new InvalidDataException("安装文件解压后超过大小限制。 ");
                    string destination = Path.GetFullPath(Path.Combine(root, entry.FullName));
                    if (!destination.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("安装包包含不安全路径。 ");
                    int unixType = (entry.ExternalAttributes >> 16) & 0xF000;
                    if (unixType == 0xA000)
                        throw new InvalidDataException("安装包不能包含符号链接。 ");
                    if (String.IsNullOrEmpty(entry.Name))
                    {
                        Directory.CreateDirectory(destination);
                    }
                    else
                    {
                        string parent = Path.GetDirectoryName(destination);
                        if (!String.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                        entry.ExtractToFile(destination, true);
                    }
                }
            }
        }

        private static void RequirePayload(string staging)
        {
            foreach (string name in new[] { "NovelAgentStudio.exe", "NovelAgentStudioConsole.exe", "Uninstall.exe" })
            {
                if (!File.Exists(Path.Combine(staging, name)))
                    throw new InvalidDataException("安装 payload 不完整：" + name);
            }
        }

        private static void ReplaceInstallDirectory(string staging)
        {
            string expected = Path.GetFullPath(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs",
                AppFolder
            ));
            string target = Path.GetFullPath(InstallDirectory);
            if (!String.Equals(expected, target, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("拒绝写入非预期安装目录。 ");
            Directory.CreateDirectory(Path.GetDirectoryName(target));
            if (Directory.Exists(target)) Directory.Delete(target, true);
            Directory.Move(staging, target);
        }

        private static void CreateShortcuts(bool desktopShortcut)
        {
            string startFolder = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.Programs),
                DisplayName
            );
            Directory.CreateDirectory(startFolder);
            CreateShortcut(
                Path.Combine(startFolder, DisplayName + ".lnk"),
                Path.Combine(InstallDirectory, "NovelAgentStudio.exe"),
                "小说智能体工作室"
            );
            CreateShortcut(
                Path.Combine(startFolder, "卸载 " + DisplayName + ".lnk"),
                Path.Combine(InstallDirectory, "Uninstall.exe"),
                "卸载小说智能体工作室"
            );
            string desktop = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                DisplayName + ".lnk"
            );
            if (desktopShortcut)
                CreateShortcut(desktop, Path.Combine(InstallDirectory, "NovelAgentStudio.exe"), "小说智能体工作室");
            else if (File.Exists(desktop))
                File.Delete(desktop);
        }

        private static void CreateShortcut(string shortcutPath, string targetPath, string description)
        {
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType == null) throw new InvalidOperationException("Windows Shortcut 服务不可用。 ");
            object shell = Activator.CreateInstance(shellType);
            object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { shortcutPath });
            Type type = shortcut.GetType();
            type.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { targetPath });
            type.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { InstallDirectory });
            type.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { description });
            type.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { targetPath + ",0" });
            type.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
            if (Marshal.IsComObject(shortcut)) Marshal.FinalReleaseComObject(shortcut);
            if (Marshal.IsComObject(shell)) Marshal.FinalReleaseComObject(shell);
        }

        private static void RegisterUninstaller()
        {
            string keyPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\NovelAgentStudio";
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(keyPath))
            {
                if (key == null) throw new InvalidOperationException("无法登记卸载信息。 ");
                key.SetValue("DisplayName", DisplayName);
                key.SetValue("DisplayVersion", Version);
                key.SetValue("Publisher", "Novel Agent Studio");
                key.SetValue("InstallLocation", InstallDirectory);
                key.SetValue("DisplayIcon", Path.Combine(InstallDirectory, "NovelAgentStudio.exe"));
                key.SetValue("UninstallString", "\"" + Path.Combine(InstallDirectory, "Uninstall.exe") + "\"");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                long bytes = DirectorySize(new DirectoryInfo(InstallDirectory));
                key.SetValue("EstimatedSize", (int)Math.Min(Int32.MaxValue, bytes / 1024), RegistryValueKind.DWord);
            }
        }

        private static long DirectorySize(DirectoryInfo directory)
        {
            long total = 0;
            foreach (FileInfo file in directory.GetFiles()) total += file.Length;
            foreach (DirectoryInfo child in directory.GetDirectories()) total += DirectorySize(child);
            return total;
        }

        internal static void WriteError(Exception error)
        {
            string path = Path.Combine(Path.GetTempPath(), "NovelAgentStudio-install-error.log");
            File.WriteAllText(path, error.ToString(), Encoding.UTF8);
        }
    }
}
