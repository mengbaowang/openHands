#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简化版串口数据读取器
适合快速使用和学习
"""

import serial
import serial.tools.list_ports
import time
from datetime import datetime


def list_serial_ports():
    """列出所有可用的串口"""
    ports = serial.tools.list_ports.comports()
    
    if not ports:
        print("未发现可用的串口设备")
        return []
    
    print("\n可用的串口设备:")
    for i, port in enumerate(ports, 1):
        print(f"{i}. {port.device} - {port.description}")
    
    return [port.device for port in ports]


def read_serial_data(port_name, baudrate=9600, duration=10):
    """
    读取串口数据
    
    Args:
        port_name: 串口名称，如 'COM1' 或 '/dev/ttyUSB0'
        baudrate: 波特率，默认9600
        duration: 读取持续时间（秒），默认10秒
    """
    try:
        # 打开串口
        ser = serial.Serial(
            port=port_name,
            baudrate=baudrate,
            timeout=1,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE
        )
        
        print(f"成功连接到串口: {port_name}")
        print(f"波特率: {baudrate}")
        print(f"读取时间: {duration}秒")
        print("-" * 50)
        
        start_time = time.time()
        data_count = 0
        
        while time.time() - start_time < duration:
            if ser.in_waiting > 0:
                # 读取一行数据
                raw_data = ser.readline()
                try:
                    # 解码数据
                    data = raw_data.decode('utf-8', errors='ignore').strip()
                    if data:
                        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        print(f"[{timestamp}] {data}")
                        data_count += 1
                except UnicodeDecodeError:
                    # 如果解码失败，显示原始字节
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 原始数据: {raw_data}")
                    data_count += 1
            
            time.sleep(0.01)  # 短暂延时，避免CPU占用过高
        
        print("-" * 50)
        print(f"读取完成，共接收 {data_count} 条数据")
        
        # 关闭串口
        ser.close()
        
    except serial.SerialException as e:
        print(f"串口错误: {e}")
    except KeyboardInterrupt:
        print("\n用户中断，正在关闭串口...")
        if 'ser' in locals() and ser.is_open:
            ser.close()
    except Exception as e:
        print(f"未知错误: {e}")


def send_data_to_serial(port_name, data, baudrate=9600):
    """
    向串口发送数据
    
    Args:
        port_name: 串口名称
        data: 要发送的数据
        baudrate: 波特率
    """
    try:
        ser = serial.Serial(port_name, baudrate, timeout=1)
        
        # 确保数据以换行符结尾
        if not data.endswith('\n'):
            data += '\n'
        
        # 发送数据
        bytes_sent = ser.write(data.encode('utf-8'))
        ser.flush()
        
        print(f"成功发送 {bytes_sent} 字节数据: {data.strip()}")
        
        ser.close()
        
    except Exception as e:
        print(f"发送数据失败: {e}")


def main():
    """主函数 - 简单的交互式界面"""
    print("=== 简化版串口数据读取器 ===")
    
    # 列出可用串口
    available_ports = list_serial_ports()
    
    if not available_ports:
        return
    
    # 选择串口
    while True:
        try:
            choice = input(f"\n请选择串口 (1-{len(available_ports)}) 或输入 'q' 退出: ")
            if choice.lower() == 'q':
                return
            
            port_index = int(choice) - 1
            if 0 <= port_index < len(available_ports):
                selected_port = available_ports[port_index]
                break
            else:
                print("无效选择，请重新输入")
        except ValueError:
            print("请输入有效数字")
    
    # 设置波特率
    while True:
        try:
            baudrate_input = input("请输入波特率 (默认9600): ").strip()
            if not baudrate_input:
                baudrate = 9600
            else:
                baudrate = int(baudrate_input)
            break
        except ValueError:
            print("请输入有效的波特率")
    
    # 选择操作
    print("\n请选择操作:")
    print("1. 读取数据")
    print("2. 发送数据")
    
    while True:
        try:
            operation = input("请选择 (1 或 2): ").strip()
            if operation in ['1', '2']:
                break
            else:
                print("请输入 1 或 2")
        except ValueError:
            print("请输入有效选择")
    
    if operation == '1':
        # 读取数据
        while True:
            try:
                duration_input = input("请输入读取时间（秒，默认10）: ").strip()
                if not duration_input:
                    duration = 10
                else:
                    duration = int(duration_input)
                break
            except ValueError:
                print("请输入有效的时间")
        
        print(f"\n开始从 {selected_port} 读取数据...")
        read_serial_data(selected_port, baudrate, duration)
        
    elif operation == '2':
        # 发送数据
        data_to_send = input("请输入要发送的数据: ")
        send_data_to_serial(selected_port, data_to_send, baudrate)


if __name__ == "__main__":
    main()