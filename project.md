# 电费监控精灵 — 项目全记录

## 一、需求背景

出租屋（天娇苑3栋1104主卧）电费通过微信小程序「辰域智控」缴纳。房东给了一个专用二维码，微信扫码后直接进入缴费主界面，无需登录。用户希望写一个 Windows 后台脚本/GUI，自动定时查询剩余电费 & 电量，低于阈值时发短信到手机（13996926630）提醒缴费。

核心约束：
- 不能依赖手机、不能依赖微信、不能依赖任何人工扫码
- 程序必须独立在 Windows 上自主运行
- 告警走短信，不走弹窗（弹窗没人看）

---

## 二、核心难题：如何绕过微信 OAuth 认证

### 2.1 平台分析

- 平台：辰域智控（`www.wap.cnyiot.com`），ASP.NET 后端，HTTP 明文
- 正常流程：微信扫二维码 → 微信浏览器打开 → 服务端触发 `snsapi_base` OAuth → 回调到 `pay.aspx?code=xxx&state=METERID` → 返回页面
- 二维码实际指向：`http://www.wap.cnyiot.com/nat/pay.aspx`（不带任何参数）

### 2.2 第一次尝试：mitmproxy 抓包

在 Windows 上安装 mitmproxy 进行 HTTPS 抓包，捕获微信小程序请求。从抓包中看到了完整请求：

```
GET http://www.wap.cnyiot.com/nat/pay.aspx?code=081GpvGa1OEXJL07ZhGa1NosEw4GpvGY&state=19500815873
```

返回的 HTML 中包含电费信息（95.72元、119.65kWh）。**关键观察**：`state` 参数的值 `19500815873` 看起来像是一个电表 ID。

### 2.3 关键发现：`mid` 参数无需认证

`code` 是微信 OAuth 返回的一次性授权码，每次扫码都不同，无法复用。但深入研究后发现：**把 `code` 换成 `mid`（Meter ID）直接访问，不需要任何 Cookie 或认证，服务端直接返回完整页面！**

```
http://www.wap.cnyiot.com/nat/pay.aspx?mid=19500815873
```

这就是整个项目得以成立的基石。一个本需要微信 OAuth 的页面，通过参数名变换（`code` → `mid`）就绕过了所有认证。

### 2.4 为什么 `mid` 能工作？

推测后端逻辑：
- `pay.aspx` 接收 `code` 参数时：通过微信 OAuth 换取 openid，再关联到电表
- `pay.aspx` 接收 `mid` 参数时：直接按电表 ID 查询，可能是一个内部/调试接口，没有做权限校验

这种「隐藏接口无需鉴权」的情况在国内小型 IoT 平台中并不少见。

---

## 三、页面解析

### 3.1 HTML 结构

返回的 HTML 中关键字段：
- 表名：`表&ensp;&ensp;&ensp;&ensp;称: <label>天娇苑3栋1104主卧</label>`
- 剩余金额：`剩余金额: <label>95.72</label>`
- 剩余电量：`剩余电量: <label>119.65</label>`

### 3.2 正则匹配踩坑

| 问题 | 原因 | 解决 |
|------|------|------|
| `表\s*名\s*称` 匹配不到 | HTML 中有 `&ensp;` 实体，不是空白字符 | 改为 `表.{0,20}称` |
| 中文乱码 | 服务端声明 `charset=utf-8` 但部分内容编码不一致 | 直接用 `decode('utf-8')` 可行 |

---

## 四、短信通知方案演进

### 4.1 方案对比

| 方案 | 可行性 | 问题 |
|------|--------|------|
| 直接调用短信 API（阿里云/腾讯云） | ❌ | 需要企业资质、签名报备、模板审核，个人无法使用 |
| 运营商邮箱网关（QQ邮箱 → 139邮箱 → 短信） | ✅ | 免费、无需资质、QQ邮箱和139邮箱都是现成的 |

### 4.2 最终方案：QQ SMTP → 139 邮箱 → 短信

```
electricity_gui.py
  → smtplib.SMTP_SSL("smtp.qq.com", 465)
  → QQ邮箱 发送邮件到 13996926630@139.com
  → 139邮箱触发"短信通知"功能
  → 中国移动下发短信到手机
```

### 4.3 短信发送失败的排查过程

**第一步：确认 QQ SMTP 是否成功**

代码中 `smtplib.sendmail()` 返回了成功，日志没有报错 → SMTP 层面没问题。

**第二步：确认 139 邮箱是否收到邮件**

用户登录 139 邮箱网页版 → 能收到 QQ 邮箱发来的邮件 → 邮件投递正常。

**第三步：为什么收到了邮件但没有短信？**

关键问题：**139 邮箱的「短信通知」功能需要手动开启！**

路径：139 邮箱 → 设置 → 短信通知 → 开启「新邮件短信提醒」。默认是关闭的，所以邮件到了但不会触发短信。

**第四步：QQ 授权码问题**

用户生成过两个授权码：
- `kbkqezzgvmecbide` — 第一次生成，不确定是否有效
- `fkcfsfvfzjkpbhfb` — 重新生成，最终使用这个

### 4.4 为什么选择邮箱网关而非直接短信 API？

向用户解释的理由：
1. **零成本**：QQ邮箱 + 139邮箱都免费，短信接收也免费
2. **零门槛**：不需要企业营业执照、不需要签名报备、不需要模板审核
3. **够用**：告警短信量极少（一天几条），完全在免费额度内
4. **可靠**：QQ邮箱 SMTP 非常稳定，139 邮箱是中国移动官方服务

---

## 五、程序架构

### 5.1 技术选型

- **语言**：Python 3.14（标准库优先，最小化依赖）
- **GUI**：tkinter/ttk（Python 自带，无需 pip install）
- **HTTP**：urllib.request（标准库）
- **打包**：PyInstaller 6.20（--onefile --windowed）

### 5.2 文件结构

```
Chargingfee/
├── electricity_gui.py          # 主程序
├── check_electricity.py        # 早期控制台版本（参考）
├── xiaomai.png                 # 窗口图标（运行时，打包进exe）
├── xiaomai.ico                 # exe文件图标（仅PyInstaller用）
├── electricity_config.json     # 配置文件（运行时生成）
├── log.txt                     # 查询日志（运行时生成）
├── project.md                  # 本文档
└── dist/
    └── electric.exe            # 打包好的可执行文件
```

### 5.3 核心设计决策

**定时调度：tkinter `after()` 而非 `threading.Event`**

最初用 `while` + `threading.Event.wait()` 做定时循环，结果界面不能实时更新。原因：后台线程不能直接操作 tkinter 控件。重构为：
- 主线程通过 `root.after(ms, callback)` 调度下次查询
- HTTP 请求在 daemon 线程中执行，完成后通过 `root.after(0, cb)` 回到主线程更新 UI
- 用 `threading.Lock()` 防止并发查询

**PyInstaller 路径处理**

`--onefile` 模式下，exe 运行时会把打包文件解压到临时目录 `sys._MEIPASS`：
- `BASE_DIR` = `os.path.dirname(sys.executable)` → exe 所在目录（可写，存 config.json / log.txt）
- `RES_DIR` = `sys._MEIPASS` → 临时解压目录（只读，取 xiaomai.png）
- 这两者必须分开处理，否则打包后找不到资源文件

**窗口图标**

- tkinter 的 `iconbitmap()` 在 Windows 上只支持 `.ico` 格式
- `iconphoto()` 支持 `.png`，通过 `tk.PhotoImage` 加载
- PyInstaller `--icon` 仍然需要 `.ico`（Windows exe 资源限制）

**告警策略**

用户选择：只要低于阈值就发送短信（不用 `alerted` 标志位去重）。理由：每一条告警都值得关注。

---

## 六、最终效果

- 每 10 分钟（可配置 1~1440 分钟）自动查询一次
- 余额 < 阈值（可配置 10~15 元）或电量 < 阈值（可配置 12.5~18.75 kWh）时发送短信
- 每次查询写入 log.txt
- 窗口显示：电表名称、剩余金额、剩余电量、上次检查时间、下次检查倒计时
- 可保存设置、手动刷新
- 打包为单个 `electric.exe`，12MB，无需安装 Python

---

## 七、关键经验总结

1. **隐藏接口是无鉴权系统的最薄弱环节**：`code` → `mid` 一个参数名的变化就绕过了微信 OAuth。做安全审计时，测试参数名变体是一个有效的技巧。

2. **短信下发链路中每个环节都可能沉默失败**：SMTP 成功 ≠ 邮件到达 ≠ 短信发送。139 邮箱的短信通知开关是隐藏的断点，靠用户手动登录邮箱才定位到。

3. **PyInstaller `--onefile` 的文件路径是最容易出错的点**：`__file__`、`sys.executable`、`sys._MEIPASS` 三者在开发模式和 frozen 模式下指向完全不同。可写文件和只读资源文件必须分目录处理。

4. **Python 标准库能做的事远比想象的多**：整个项目零 pip 依赖（除了 PyInstaller 打包），tkinter + urllib + smtplib + json + re 就搞定了 GUI、HTTP、邮件、配置、解析的全部需求。
