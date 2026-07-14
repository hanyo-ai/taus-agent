---
name: ble-scanner
description: 扫描 Bryxen ESP32-C6 设备的 BLE 广播数据（计数/运行时间/温度/LED频率），并通过 GATT 写入发送 LED 控制指令（on/off/auto）
---

# Bryxen BLE Scanner

通过 `bleak` 库扫描 Bryxen ESP32-C6 (`Bryxen-C6`) 的 BLE 广播，解析厂商自定义数据；也可连接设备并写入 GATT 特征值发送 LED 控制指令。

## 依赖
```
source .venv/bin/activate
```
```bash
uv add bleak
```

## 关键参数

```python
TARGET_NAME = "Bryxen-C6"
COMPANY_ID  = 0xFFFF
CTRL_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
```

广播的 manufacturer data（company id `0xFFFF`）按小端打包为 `<HIHB`（9 字节）：

| 字段 | 类型 | 说明 |
|---|---|---|
| cnt | uint16 | 广播计数，用于去重 |
| uptime | uint32 | 设备运行时间（秒） |
| tmp_raw | uint16 | 温度，实际值 = tmp_raw / 100 (°C) |
| freq_raw | uint8 | LED 频率，实际值 = freq_raw / 10 (Hz) |

## 1. 持续扫描广播

```python
import asyncio, struct
from bleak import BleakScanner

TARGET_NAME = "Bryxen-C6"
COMPANY_ID = 0xFFFF

async def scan():
    last_cnt = -1

    def cb(device, ad):
        nonlocal last_cnt
        if ad.local_name == TARGET_NAME and COMPANY_ID in ad.manufacturer_data:
            data = ad.manufacturer_data[COMPANY_ID]
            if len(data) >= 9:
                cnt, uptime, tmp_raw, freq_raw = struct.unpack('<HIHB', data[:9])
                if cnt != last_cnt:
                    last_cnt = cnt
                    print(f"#{cnt:>4} | Uptime:{uptime:>4}s | "
                          f"Temp:{tmp_raw/100:>5.1f}°C | "
                          f"LED:{freq_raw/10:.1f}Hz | "
                          f"RSSI:{getattr(ad,'rssi','?')}dBm")

    async with BleakScanner(cb) as scanner:
        await asyncio.Event().wait()  # Ctrl+C 停止

asyncio.run(scan())
```

## 2. 发送 LED 控制指令

指令只能是 `ON` / `OFF` / `AUTO`（大写字节串）。先按名称查找设备，再连接并写入 GATT 特征值。

```python
import asyncio
from bleak import BleakScanner, BleakClient

TARGET_NAME = "Bryxen-C6"
CTRL_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

async def send_command(cmd: str):
    cmd_bytes = cmd.strip().upper().encode()
    if cmd_bytes not in (b"ON", b"OFF", b"AUTO"):
        print(f"未知指令: {cmd!r} (可用: on / off / auto)")
        return

    device = await BleakScanner.find_device_by_name(TARGET_NAME, timeout=10.0)
    if device is None:
        print("未找到设备，请确认广播正在运行。")
        return

    async with BleakClient(device) as client:
        await client.write_gatt_char(CTRL_CHAR_UUID, cmd_bytes)
        print(f"已发送: {cmd_bytes.decode()}")

asyncio.run(send_command("on"))
```

## 适用场景

- 监控 Bryxen ESP32-C6 设备的实时状态（温度、运行时间、LED 频率）
- 远程切换设备 LED 模式（常开 / 常关 / 自动）
- 调试 BLE 广播数据格式或 GATT 写入是否正常

## 注意事项

- 扫描和连接需要蓝牙权限，macOS 上需在系统设置中授予终端/IDE 蓝牙访问权限。
- `find_device_by_name` 超时默认 10 秒，设备不在广播状态时会返回 `None`。
- `cnt` 字段用于去重同一广播包的重复上报，仅当计数变化时才打印。
