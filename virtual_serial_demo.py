#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟串口演示程序
用于在没有真实串口设备时测试串口程序
"""

import threading
import time
import random
import queue
from datetime import datetime
from typing import Optional, Callable


class VirtualSerialDevice:
    """虚拟串口设备类"""
    
    def __init__(self, device_name: str = "Virtual_COM1"):
        self.device_name = device_name
        self.is_open = False
        self.baudrate = 9600
        self.timeout = 1.0
        self.data_queue = queue.Queue()
        self.write_queue = queue.Queue()
        self.simulation_thread = None
        self.is_simulating = False
        
    def open(self):
        """打开虚拟串口"""
        self.is_open = True
        self.is_simulating = True
        self.simulation_thread = threading.Thread(target=self._simulate_data, daemon=True)
        self.simulation_thread.start()
        print(f"虚拟串口 {self.device_name} 已打开")
        
    def close(self):
        """关闭虚拟串口"""
        self.is_simulating = False
        self.is_open = False
        if self.simulation_thread:
            self.simulation_thread.join(timeout=1.0)
        print(f"虚拟串口 {self.device_name} 已关闭")
        
    def _simulate_data(self):
        """模拟数据生成"""
        data_types = ['gps', 'sensor', 'status']
        
        while self.is_simulating:
            # 随机选择数据类型
            data_type = random.choice(data_types)
            
            if data_type == 'gps':
                # 模拟GPS数据
                lat = 39.9042 + random.uniform(-0.01, 0.01)
                lon = 116.4074 + random.uniform(-0.01, 0.01)
                data = f"$GPGGA,{datetime.now().strftime('%H%M%S')},{lat:.6f},N,{lon:.6f},E,1,08,0.9,545.4,M,46.9,M,,*47"
                
            elif data_type == 'sensor':
                # 模拟传感器数据
                temp = random.uniform(20.0, 30.0)
                humidity = random.uniform(40.0, 80.0)
                data = f"SENSOR,TEMP:{temp:.2f},HUM:{humidity:.2f}"
                
            else:
                # 模拟状态数据
                status = random.choice(['OK', 'WARNING', 'ERROR'])
                data = f"STATUS:{status},TIME:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            self.data_queue.put(data)
            
            # 随机间隔
            time.sleep(random.uniform(0.5, 2.0))
    
    def readline(self) -> bytes:
        """读取一行数据"""
        try:
            data = self.data_queue.get(timeout=self.timeout)
            return (data + '\n').encode('utf-8')
        except queue.Empty:
            return b''
    
    def write(self, data: bytes) -> int:
        """写入数据"""
        decoded_data = data.decode('utf-8').strip()
        self.write_queue.put(decoded_data)
        print(f"虚拟串口接收到写入数据: {decoded_data}")
        return len(data)
    
    def flush(self):
        """刷新缓冲区"""
        pass
    
    @property
    def in_waiting(self) -> int:
        """返回等待读取的字节数"""
        return self.data_queue.qsize()


class VirtualSerialReader:
    """虚拟串口读取器"""
    
    def __init__(self):
        self.device = VirtualSerialDevice()
        self.data_callback = None
        self.is_reading = False
        self.read_thread = None
        self.data_buffer = []
        
    def set_data_callback(self, callback: Callable[[str, datetime], None]):
        """设置数据回调函数"""
        self.data_callback = callback
        
    def start_reading(self):
        """开始读取数据"""
        if not self.device.is_open:
            self.device.open()
            
        self.is_reading = True
        self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.read_thread.start()
        print("开始读取虚拟串口数据...")
        
    def stop_reading(self):
        """停止读取数据"""
        self.is_reading = False
        if self.read_thread:
            self.read_thread.join(timeout=2.0)
        self.device.close()
        print("停止读取虚拟串口数据")
        
    def _read_loop(self):
        """读取循环"""
        while self.is_reading:
            if self.device.in_waiting > 0:
                raw_data = self.device.readline()
                if raw_data:
                    data = raw_data.decode('utf-8').strip()
                    timestamp = datetime.now()
                    
                    # 添加到缓冲区
                    self.data_buffer.append({
                        'timestamp': timestamp,
                        'data': data
                    })
                    
                    # 调用回调函数
                    if self.data_callback:
                        self.data_callback(data, timestamp)
            
            time.sleep(0.01)
    
    def send_data(self, data: str):
        """发送数据到虚拟设备"""
        if self.device.is_open:
            self.device.write(data.encode('utf-8'))
        else:
            print("虚拟串口未打开")
    
    def get_data_buffer(self):
        """获取数据缓冲区"""
        return self.data_buffer.copy()


def demo_callback(data: str, timestamp: datetime):
    """演示回调函数"""
    print(f"[{timestamp.strftime('%H:%M:%S.%f')[:-3]}] 接收: {data}")


def main():
    """主演示函数"""
    print("=== 虚拟串口演示程序 ===")
    print("这个程序模拟串口设备，生成GPS、传感器和状态数据")
    print()
    
    # 创建虚拟串口读取器
    reader = VirtualSerialReader()
    reader.set_data_callback(demo_callback)
    
    try:
        # 开始读取
        reader.start_reading()
        
        print("虚拟串口正在生成数据...")
        print("可以输入以下命令:")
        print("  'send <数据>' - 向虚拟设备发送数据")
        print("  'status' - 显示接收到的数据数量")
        print("  'quit' - 退出程序")
        print()
        
        while True:
            try:
                command = input().strip()
                
                if command.lower() == 'quit':
                    break
                elif command.startswith('send '):
                    data_to_send = command[5:]
                    reader.send_data(data_to_send)
                elif command.lower() == 'status':
                    buffer = reader.get_data_buffer()
                    print(f"已接收 {len(buffer)} 条数据")
                elif command:
                    print("未知命令")
                    
            except EOFError:
                break
                
    except KeyboardInterrupt:
        print("\n收到中断信号...")
    finally:
        reader.stop_reading()
        print("演示程序已退出")


if __name__ == "__main__":
    main()