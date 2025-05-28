# 串口数据读取器

一个功能完整的Python串口数据获取程序，支持扫描、连接、读取和写入串口数据。

## 功能特性

- 🔍 **自动扫描串口**: 自动检测系统中所有可用的串口设备
- 📡 **数据读写**: 支持串口数据的读取和写入操作
- 🔄 **连续读取**: 支持后台线程连续读取数据
- 💾 **数据保存**: 支持将数据保存为CSV和JSON格式
- ⚙️ **灵活配置**: 支持自定义波特率、超时等参数
- 📝 **日志记录**: 完整的操作日志记录
- 🎯 **回调机制**: 支持自定义数据处理回调函数
- 🖥️ **交互模式**: 提供友好的交互式界面
- 🧪 **虚拟测试**: 提供虚拟串口用于测试

## 文件说明

| 文件名 | 说明 |
|--------|------|
| `serial_data_reader.py` | 主程序，完整的串口数据读取器类 |
| `simple_serial_reader.py` | 简化版本，适合快速使用和学习 |
| `virtual_serial_demo.py` | 虚拟串口演示，无需真实硬件即可测试 |
| `example_usage.py` | 使用示例，展示各种应用场景 |
| `serial_config.json` | 配置文件，包含默认设置 |
| `requirements.txt` | 依赖包列表 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 基本使用

#### 交互模式（推荐新手）

```bash
python serial_data_reader.py -i
```

#### 命令行模式

```bash
# 列出可用串口
python serial_data_reader.py -l

# 连接指定串口并读取数据
python serial_data_reader.py -p COM1 -b 9600

# 读取10秒并保存到CSV文件
python serial_data_reader.py -p COM1 -d 10 --csv data.csv
```

#### 简化版本

```bash
python simple_serial_reader.py
```

#### 虚拟测试（无需真实硬件）

```bash
python virtual_serial_demo.py
```

### 3. 编程使用

```python
from serial_data_reader import SerialDataReader

# 创建读取器
reader = SerialDataReader(port='COM1', baudrate=9600)

# 连接串口
if reader.connect():
    # 读取单条数据
    data = reader.read_data()
    print(f"接收到: {data}")
    
    # 发送数据
    reader.write_data("Hello Serial!")
    
    # 断开连接
    reader.disconnect()
```

## 详细功能

### 串口扫描

```python
from serial_data_reader import SerialDataReader

reader = SerialDataReader()
ports = reader.scan_ports()

for port in ports:
    print(f"设备: {port['device']}")
    print(f"描述: {port['description']}")
```

### 连续数据读取

```python
def data_callback(data, timestamp):
    print(f"[{timestamp}] {data}")

reader = SerialDataReader()
reader.set_data_callback(data_callback)
reader.connect('COM1')
reader.start_reading()

# 读取10秒
time.sleep(10)

reader.stop_reading()
reader.disconnect()
```

### 数据保存

```python
# 保存为CSV格式
reader.save_data_to_csv('serial_data.csv')

# 保存为JSON格式
reader.save_data_to_json('serial_data.json')
```

## 命令行参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `-p, --port` | 串口名称 | `-p COM1` |
| `-b, --baudrate` | 波特率 | `-b 115200` |
| `-t, --timeout` | 超时时间 | `-t 2.0` |
| `-l, --list` | 列出可用串口 | `-l` |
| `-i, --interactive` | 交互模式 | `-i` |
| `-d, --duration` | 读取时间（秒） | `-d 30` |
| `--csv` | 保存为CSV文件 | `--csv data.csv` |
| `--json` | 保存为JSON文件 | `--json data.json` |

## 配置文件

`serial_config.json` 包含默认配置：

```json
{
  "default_settings": {
    "baudrate": 9600,
    "timeout": 1.0,
    "encoding": "utf-8"
  },
  "common_baudrates": [9600, 115200, 230400],
  "data_formats": {
    "csv": {"enabled": true},
    "json": {"enabled": true}
  }
}
```

## 常见应用场景

### 1. GPS数据采集

```python
reader = SerialDataReader(port='COM3', baudrate=4800)
reader.connect()

def gps_handler(data, timestamp):
    if data.startswith('$GPGGA'):
        print(f"GPS定位数据: {data}")

reader.set_data_callback(gps_handler)
reader.start_reading()
```

### 2. 传感器数据监控

```python
reader = SerialDataReader(port='COM1', baudrate=9600)
reader.connect()

def sensor_handler(data, timestamp):
    if 'TEMP' in data:
        # 解析温度数据
        temp = float(data.split(':')[1])
        if temp > 30:
            print(f"高温警告: {temp}°C")

reader.set_data_callback(sensor_handler)
reader.start_reading()
```

### 3. 设备通信

```python
reader = SerialDataReader(port='COM2', baudrate=115200)
reader.connect()

# 发送AT命令
reader.write_data('AT+VERSION')
time.sleep(1)

# 读取响应
response = reader.read_data()
print(f"设备版本: {response}")
```

## 错误处理

程序包含完整的错误处理机制：

- 串口连接失败
- 数据读写错误
- 编码解码错误
- 文件保存错误

所有错误都会记录到日志文件 `serial_data.log` 中。

## 系统兼容性

- ✅ Windows (COM1, COM2, ...)
- ✅ Linux (/dev/ttyUSB0, /dev/ttyACM0, ...)
- ✅ macOS (/dev/cu.usbserial, ...)

## 常见问题

### Q: 找不到串口设备？
A: 
1. 检查设备是否正确连接
2. 确认驱动程序已安装
3. 使用 `-l` 参数列出可用串口

### Q: 连接失败？
A:
1. 检查串口是否被其他程序占用
2. 确认波特率设置正确
3. 检查串口权限（Linux/macOS）

### Q: 数据乱码？
A:
1. 检查波特率是否匹配
2. 尝试不同的编码格式
3. 检查数据位、停止位、校验位设置

### Q: 没有真实串口设备怎么测试？
A: 使用 `virtual_serial_demo.py` 进行虚拟测试

## 许可证

MIT License

## 贡献

欢迎提交Issue和Pull Request！

## 更新日志

- v1.0.0: 初始版本，包含基本串口读写功能
- 支持数据保存、日志记录、交互模式
- 提供虚拟串口测试功能