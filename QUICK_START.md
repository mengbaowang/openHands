# 快速开始指南

## 5分钟上手串口数据读取器

### 第一步：安装依赖

```bash
pip install pyserial
```

### 第二步：选择使用方式

#### 方式1：交互模式（最简单）

```bash
python serial_data_reader.py -i
```

按照提示操作即可！

#### 方式2：简化版本

```bash
python simple_serial_reader.py
```

#### 方式3：虚拟测试（无需硬件）

```bash
python virtual_serial_demo.py
```

### 第三步：基本操作

1. **查看可用串口**
   ```bash
   python serial_data_reader.py -l
   ```

2. **连接串口读取数据**
   ```bash
   python serial_data_reader.py -p COM1 -b 9600
   ```

3. **读取并保存数据**
   ```bash
   python serial_data_reader.py -p COM1 -d 10 --csv data.csv
   ```

### 编程示例

```python
from serial_data_reader import SerialDataReader

# 创建并连接
reader = SerialDataReader()
reader.connect('COM1')

# 读取数据
data = reader.read_data()
print(data)

# 发送数据
reader.write_data('Hello!')

# 断开连接
reader.disconnect()
```

### 常用参数

- `-p COM1`: 指定串口
- `-b 9600`: 设置波特率
- `-d 10`: 读取10秒
- `-i`: 交互模式
- `-l`: 列出串口

就这么简单！更多功能请查看 [完整文档](SERIAL_README.md)。