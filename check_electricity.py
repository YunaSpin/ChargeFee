import re
import urllib.request
import time
import subprocess

# ===== 配置 =====
METER_ID = "19500815873"
URL = "http://www.wap.cnyiot.com/nat/pay.aspx?mid=" + METER_ID
CHECK_INTERVAL = 600  # 检查间隔(秒), 默认10分钟
BALANCE_THRESHOLD = 10  # 余额低于多少元告警
KWH_THRESHOLD = 15  # 电量低于多少kWh告警
# ================

def query():
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
    except Exception as e:
        print(f"[ERROR] {e}")
        return None

    kwh = re.search(r'剩余电量:[\s\S]*?<label[^>]*>([0-9.]+)</label>', html)
    money = re.search(r'剩余金额:[\s\S]*?<label[^>]*>([0-9.]+)</label>', html)
    name = re.search(r'表.{0,20}称:[\s\S]*?<label[^>]*>([^<]+)</label>', html)

    if not money:
        return None

    return {
        "name": name.group(1).strip() if name else "",
        "kwh": float(kwh.group(1)) if kwh else 0,
        "money": float(money.group(1)),
        "price": 0
    }

def notify(title, message):
    # Windows 10/11 toast notification via PowerShell (no extra dependencies)
    try:
        subprocess.run(["powershell", "-Command", f'''
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
            $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $txt = $tpl.GetElementsByTagName("text")
            $txt.Item(0).AppendChild($tpl.CreateTextNode("{title}")) > $null
            $txt.Item(1).AppendChild($tpl.CreateTextNode("{message}")) > $null
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("ElectricityMonitor").Show($tpl)
        '''], capture_output=True, timeout=10)
    except:
        pass  # silent fail if notification not available

def main():
    print("=" * 45)
    print(f"  Meter: {METER_ID}")
    print(f"  Interval: {CHECK_INTERVAL}s | Alert: balance<{BALANCE_THRESHOLD} CNY, kwh<{KWH_THRESHOLD} kWh")
    print("=" * 45)

    alerted = False

    while True:
        data = query()
        ts = time.strftime("%H:%M:%S")

        if data is None:
            print(f"[{ts}] query failed, retry next cycle...")
            time.sleep(CHECK_INTERVAL)
            continue

        name = data["name"]
        kwh = data["kwh"]
        money = data["money"]

        print(f"[{ts}] {name} | balance: {money} CNY, kwh: {kwh} kWh")

        low_balance = money <= BALANCE_THRESHOLD
        low_kwh = kwh <= KWH_THRESHOLD

        if (low_balance or low_kwh) and not alerted:
            notify("LOW ELECTRICITY WARNING",
                   f"Balance: {money} CNY ({kwh} kWh)\nPlease recharge soon!")
            alerted = True
        elif not low_balance and not low_kwh:
            alerted = False

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
