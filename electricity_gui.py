import re
import json
import os
import sys
import time
import threading
import smtplib
from email.mime.text import MIMEText
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
import urllib.request

# ===== 文件路径 =====
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)  # PyInstaller onefile: exe所在目录(可写)
    RES_DIR = sys._MEIPASS                       # PyInstaller 资源解压临时目录(只读)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RES_DIR = BASE_DIR
CONFIG_FILE = os.path.join(BASE_DIR, "electricity_config.json")
LOG_FILE = os.path.join(BASE_DIR, "log.txt")
ICON_FILE = os.path.join(RES_DIR, "xiaomai.png")

# ===== 默认配置 =====
DEFAULT_CONFIG = {
    "meter_id": "19500815873",
    "check_interval_minutes": 10,
    "balance_threshold": 10.0,
    "kwh_threshold": 15.0,
}

# ===== 校验范围 =====
INTERVAL_MIN, INTERVAL_MAX = 1, 1440       # 分钟
BALANCE_MIN, BALANCE_MAX = 10.0, 15      # 元
KWH_MIN, KWH_MAX = 12.5, 18.75             # kWh

URL_TEMPLATE = "http://www.wap.cnyiot.com/nat/pay.aspx?mid={}"

# ===== 短信通知配置 =====
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465
SMTP_USER = "404791407@qq.com"
SMTP_PASS = "fkcfsfvfzjkpbhfb"
SMS_RECEIVER = "13996926630@139.com"  # 139 = 中国移动
SMS_MESSAGE = "没电费了，贴汁，快充！"


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def validate_interval(val):
    try:
        v = float(val)
        if INTERVAL_MIN <= v <= INTERVAL_MAX:
            return v, None
        return None, f"检查间隔必须为 {INTERVAL_MIN}~{INTERVAL_MAX} 分钟"
    except ValueError:
        return None, "请输入有效的数字"


def validate_balance(val):
    try:
        v = float(val)
        if BALANCE_MIN < v < BALANCE_MAX:
            return v, None
        return None, f"余额阈值必须 >{BALANCE_MIN} 且 <{BALANCE_MAX} 元"
    except ValueError:
        return None, "请输入有效的数字"


def validate_kwh(val):
    try:
        v = float(val)
        if KWH_MIN < v < KWH_MAX:
            return v, None
        return None, f"电量阈值必须 >{KWH_MIN} 且 <{KWH_MAX} kWh"
    except ValueError:
        return None, "请输入有效的数字"


def query_meter(meter_id):
    try:
        url = URL_TEMPLATE.format(meter_id)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
    except Exception as e:
        return None, str(e)

    kwh_match = re.search(r'剩余电量:[\s\S]*?<label[^>]*>([0-9.]+)</label>', html)
    money_match = re.search(r'剩余金额:[\s\S]*?<label[^>]*>([0-9.]+)</label>', html)
    name_match = re.search(r'表.{0,20}称:[\s\S]*?<label[^>]*>([^<]+)</label>', html)

    if not money_match:
        return None, "解析失败"

    return {
        "name": name_match.group(1).strip() if name_match else "",
        "kwh": float(kwh_match.group(1)) if kwh_match else 0,
        "money": float(money_match.group(1)),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, None


def log_check(data, alert=False):
    """追加日志到当前目录下的 log.txt"""
    try:
        line = f"[{data['time']}] {data['name']} | 余额:{data['money']:.2f}元 | 电量:{data['kwh']:.2f}kWh"
        if alert:
            line += " | *** 低于阈值，已发送短信 ***"
        line += "\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[LOG ERROR] {e}")


def send_sms():
    """通过QQ邮箱SMTP发送短信到手机（运营商邮箱网关）"""
    try:
        msg = MIMEText(SMS_MESSAGE, "plain", "utf-8")
        msg["From"] = SMTP_USER
        msg["To"] = SMS_RECEIVER
        msg["Subject"] = "电费告警"

        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [SMS_RECEIVER], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] SMS发送失败: {e}\n")
        return False


class ElectricityMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("电费监控")
        self.root.resizable(False, False)
        if os.path.exists(ICON_FILE):
            self.root.iconphoto(True, tk.PhotoImage(file=ICON_FILE))

        self.cfg = load_config()
        self.current_data = None
        self.running = True
        self.alerted = False
        self._next_check_id = None
        self._query_lock = threading.Lock()

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 窗口居中
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")

        # 启动首次查询
        self._trigger_query()

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.grid(row=0, column=0, sticky="nsew")

        # === 当前状态区 ===
        status_frame = ttk.LabelFrame(main_frame, text=" 当前状态 ", padding=15)
        status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 15))

        self.lbl_name = ttk.Label(status_frame, text="正在查询...", font=("Microsoft YaHei UI", 11))
        self.lbl_name.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Label(status_frame, text="剩余金额:", font=("Microsoft YaHei UI", 10)).grid(row=1, column=0, sticky="w")
        self.lbl_money = ttk.Label(status_frame, text="--", font=("Microsoft YaHei UI", 18, "bold"), foreground="#0078D4")
        self.lbl_money.grid(row=1, column=1, sticky="e", padx=(30, 0))

        ttk.Label(status_frame, text="元").grid(row=1, column=2, padx=(5, 20))

        ttk.Label(status_frame, text="剩余电量:", font=("Microsoft YaHei UI", 10)).grid(row=1, column=3, sticky="w")
        self.lbl_kwh = ttk.Label(status_frame, text="--", font=("Microsoft YaHei UI", 18, "bold"), foreground="#107C10")
        self.lbl_kwh.grid(row=1, column=4, sticky="e", padx=(30, 0))

        ttk.Label(status_frame, text="kWh").grid(row=1, column=5, padx=(5, 0))

        ttk.Label(status_frame, text="上次检查:", font=("Microsoft YaHei UI", 9), foreground="#666").grid(row=2, column=0, columnspan=6, sticky="w", pady=(10, 0))
        self.lbl_time = ttk.Label(status_frame, text="尚未查询", font=("Microsoft YaHei UI", 9), foreground="#666")
        self.lbl_time.grid(row=2, column=1, columnspan=5, sticky="w", pady=(10, 0))

        # 下次检查倒计时
        ttk.Label(status_frame, text="下次检查:", font=("Microsoft YaHei UI", 9), foreground="#666").grid(row=3, column=0, columnspan=6, sticky="w", pady=(2, 0))
        self.lbl_next = ttk.Label(status_frame, text="--", font=("Microsoft YaHei UI", 9), foreground="#666")
        self.lbl_next.grid(row=3, column=1, columnspan=5, sticky="w", pady=(2, 0))

        # === 设置区 ===
        settings_frame = ttk.LabelFrame(main_frame, text=" 告警设置 ", padding=15)
        settings_frame.grid(row=1, column=0, sticky="ew", pady=(0, 15))

        ttk.Label(settings_frame, text="检查时间间隔 (分钟):", font=("Microsoft YaHei UI", 10)).grid(row=0, column=0, sticky="w", pady=5)
        self.entry_interval = ttk.Entry(settings_frame, width=10, font=("Microsoft YaHei UI", 10))
        self.entry_interval.grid(row=0, column=1, sticky="w", padx=(10, 5), pady=5)
        self.entry_interval.insert(0, str(self.cfg["check_interval_minutes"]))
        ttk.Label(settings_frame, text=f"({INTERVAL_MIN}~{INTERVAL_MAX} 分钟)", foreground="#666", font=("Microsoft YaHei UI", 8)).grid(row=0, column=2, sticky="w", pady=5)

        ttk.Label(settings_frame, text="余额警告阈值 (元):", font=("Microsoft YaHei UI", 10)).grid(row=1, column=0, sticky="w", pady=5)
        self.entry_balance = ttk.Entry(settings_frame, width=10, font=("Microsoft YaHei UI", 10))
        self.entry_balance.grid(row=1, column=1, sticky="w", padx=(10, 5), pady=5)
        self.entry_balance.insert(0, str(self.cfg["balance_threshold"]))
        ttk.Label(settings_frame, text=f"(>{BALANCE_MIN} 且 <{BALANCE_MAX} 元)", foreground="#666", font=("Microsoft YaHei UI", 8)).grid(row=1, column=2, sticky="w", pady=5)

        ttk.Label(settings_frame, text="电量警告阈值 (kWh):", font=("Microsoft YaHei UI", 10)).grid(row=2, column=0, sticky="w", pady=5)
        self.entry_kwh = ttk.Entry(settings_frame, width=10, font=("Microsoft YaHei UI", 10))
        self.entry_kwh.grid(row=2, column=1, sticky="w", padx=(10, 5), pady=5)
        self.entry_kwh.insert(0, str(self.cfg["kwh_threshold"]))
        ttk.Label(settings_frame, text=f"(>{KWH_MIN} 且 <{KWH_MAX} kWh)", foreground="#666", font=("Microsoft YaHei UI", 8)).grid(row=2, column=2, sticky="w", pady=5)

        btn_frame = ttk.Frame(settings_frame)
        btn_frame.grid(row=3, column=0, columnspan=3, sticky="e", pady=(10, 0))

        self.lbl_save_status = ttk.Label(btn_frame, text="", foreground="green", font=("Microsoft YaHei UI", 9))
        self.lbl_save_status.grid(row=0, column=0, padx=(0, 15))

        self.btn_save = ttk.Button(btn_frame, text="保存设置", command=self._on_save)
        self.btn_save.grid(row=0, column=1)

        # === 底部按钮区 ===
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.grid(row=2, column=0, sticky="ew")

        self.lbl_status = ttk.Label(bottom_frame, text="● 运行中", foreground="green", font=("Microsoft YaHei UI", 9))
        self.lbl_status.grid(row=0, column=0, sticky="w")

        ttk.Button(bottom_frame, text="立即刷新", command=self._trigger_query).grid(row=0, column=1, sticky="e", padx=(0, 10))
        ttk.Button(bottom_frame, text="退出", command=self._on_close).grid(row=0, column=2, sticky="e")

        # 日志文件路径
        ttk.Label(bottom_frame, text=f"日志: {LOG_FILE}", foreground="#999", font=("Microsoft YaHei UI", 7)).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

    def _trigger_query(self):
        """在新线程中执行查询，主线程不会被阻塞"""
        if not self.running:
            return
        # 取消之前的定时器（重新计时）
        if self._next_check_id is not None:
            self.root.after_cancel(self._next_check_id)
            self._next_check_id = None

        self.lbl_status.config(text="⟳ 查询中...", foreground="#FF8C00")
        t = threading.Thread(target=self._do_query_thread, daemon=True)
        t.start()

    def _do_query_thread(self):
        """后台线程：执行HTTP查询，完成后回到主线程更新UI"""
        acquired = self._query_lock.acquire(blocking=False)
        if not acquired:
            # 上一次查询还没结束，跳过
            self.root.after(0, lambda: self.lbl_status.config(text="● 运行中 (上次查询未完成)", foreground="green"))
            return

        try:
            data, err = query_meter(self.cfg["meter_id"])
            if err:
                self.root.after(0, self._on_query_error, err)
            else:
                self.current_data = data
                self.root.after(0, self._on_query_success, data)
        finally:
            self._query_lock.release()

    def _on_query_success(self, data):
        """主线程：更新显示"""
        self.lbl_name.config(text=data["name"] or "未知")
        self.lbl_money.config(text=f"{data['money']:.2f}")
        self.lbl_kwh.config(text=f"{data['kwh']:.2f}")
        self.lbl_time.config(text=data["time"])
        self.lbl_status.config(text="● 运行中", foreground="green")
        self.root.update_idletasks()

        # 记录日志（每次查询都记）
        log_check(data)

        self._check_alert(data)
        self._schedule_next()

    def _on_query_error(self, err):
        """主线程：显示错误"""
        self.lbl_status.config(text=f"✖ 查询失败: {err}", foreground="red")
        self.lbl_next.config(text="重试中...")
        # 出错后 30 秒重试
        if self.running:
            self._next_check_id = self.root.after(30000, self._trigger_query)

    def _schedule_next(self):
        """安排下次定时检查"""
        if not self.running:
            return
        interval_ms = int(self.cfg["check_interval_minutes"] * 60 * 1000)
        self._next_check_id = self.root.after(interval_ms, self._trigger_query)

        # 更新倒计时显示
        self._countdown_deadline = time.time() + interval_ms / 1000
        self._update_countdown()

    def _update_countdown(self):
        if not self.running or self._next_check_id is None:
            return
        remaining = getattr(self, '_countdown_deadline', 0) - time.time()
        if remaining > 0:
            m, s = divmod(int(remaining), 60)
            self.lbl_next.config(text=f"约 {m} 分 {s} 秒后")
            self.root.after(10000, self._update_countdown)  # 每10秒更新倒计时

    def _check_alert(self, data):
        balance_thresh = self.cfg["balance_threshold"]
        kwh_thresh = self.cfg["kwh_threshold"]

        money_low = data["money"] <= balance_thresh
        kwh_low = data["kwh"] <= kwh_thresh

        if (money_low or kwh_low):
            self.alerted = True
            # 发短信
            ok = send_sms()
            # 记告警日志
            log_check(data, alert=True)
            if ok:
                self.lbl_status.config(text="● 已发送短信告警", foreground="red")
            else:
                self.lbl_status.config(text="● 告警 (短信发送失败)", foreground="red")
        elif not money_low and not kwh_low:
            self.alerted = False

    def _on_save(self):
        errors = []

        iv, err = validate_interval(self.entry_interval.get())
        if err:
            errors.append(err)
        bv, err = validate_balance(self.entry_balance.get())
        if err:
            errors.append(err)
        kv, err = validate_kwh(self.entry_kwh.get())
        if err:
            errors.append(err)

        if errors:
            messagebox.showerror("输入校验失败", "\n".join(errors))
            return

        self.cfg["check_interval_minutes"] = int(iv)
        self.cfg["balance_threshold"] = bv
        self.cfg["kwh_threshold"] = kv
        save_config(self.cfg)
        self.alerted = False

        self.lbl_save_status.config(text="✓ 已保存，立即刷新...")
        self.root.after(3000, lambda: self.lbl_save_status.config(text=""))

        self._trigger_query()

    def _on_close(self):
        self.running = False
        if self._next_check_id is not None:
            self.root.after_cancel(self._next_check_id)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = ElectricityMonitorApp(root)
    root.mainloop()
