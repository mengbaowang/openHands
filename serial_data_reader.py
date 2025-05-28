#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
串口数据获取程序
功能：扫描、连接并读取本机串口数据
作者：OpenHands AI Assistant
日期：2025-05-28
"""

import serial
import serial.tools.list_ports
import time
import threading
import json
import csv
import logging
from datetime import datetime
from typing import List, Dict, Optional, Callable
import argparse
import sys


class SerialDataReader:
    """串口数据读取器类"""
    
    def __init__(self, port: str = None, baudrate: int = 9600, 
                 timeout: float = 1.0, encoding: str = 'utf-8'):
        """
        初始化串口数据读取器
        
        Args:
            port: 串口名称，如 'COM1' 或 '/dev/ttyUSB0'
            baudrate: 波特率，默认9600
            timeout: 超时时间（秒），默认1.0
            encoding: 数据编码，默认utf-8
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.encoding = encoding
        self.serial_conn = None
        self.is_reading = False
        self.data_buffer = []
        self.read_thread = None
        self.data_callback = None
        
        # 设置日志
        self.setup_logging()
        
    def setup_logging(self):
        """设置日志配置"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('serial_data.log', encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    @staticmethod
    def scan_ports() -> List[Dict[str, str]]:
        """
        扫描系统中可用的串口
        
        Returns:
            包含串口信息的字典列表
        """
        ports = []
        available_ports = serial.tools.list_ports.comports()
        
        for port in available_ports:
            port_info = {
                'device': port.device,
                'name': port.name,
                'description': port.description,
                'hwid': port.hwid,
                'vid': port.vid,
                'pid': port.pid,
                'serial_number': port.serial_number,
                'manufacturer': port.manufacturer
            }
            ports.append(port_info)
            
        return ports
    
    def print_available_ports(self):
        """打印可用串口信息"""
        ports = self.scan_ports()
        
        if not ports:
            print("未发现可用的串口设备")
            return
            
        print("\n=== 可用串口设备 ===")
        for i, port in enumerate(ports, 1):
            print(f"{i}. 设备: {port['device']}")
            print(f"   名称: {port['name']}")
            print(f"   描述: {port['description']}")
            print(f"   硬件ID: {port['hwid']}")
            if port['manufacturer']:
                print(f"   制造商: {port['manufacturer']}")
            print("-" * 40)
    
    def connect(self, port: str = None) -> bool:
        """
        连接到指定串口
        
        Args:
            port: 串口名称，如果为None则使用初始化时的端口
            
        Returns:
            连接成功返回True，失败返回False
        """
        if port:
            self.port = port
            
        if not self.port:
            self.logger.error("未指定串口")
            return False
            
        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE
            )
            
            if self.serial_conn.is_open:
                self.logger.info(f"成功连接到串口: {self.port}")
                return True
            else:
                self.logger.error(f"无法打开串口: {self.port}")
                return False
                
        except serial.SerialException as e:
            self.logger.error(f"串口连接错误: {e}")
            return False
        except Exception as e:
            self.logger.error(f"未知错误: {e}")
            return False
    
    def disconnect(self):
        """断开串口连接"""
        self.stop_reading()
        
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            self.logger.info(f"已断开串口连接: {self.port}")
    
    def read_data(self) -> Optional[str]:
        """
        读取一行串口数据
        
        Returns:
            读取到的数据字符串，如果没有数据或出错返回None
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            return None
            
        try:
            if self.serial_conn.in_waiting > 0:
                raw_data = self.serial_conn.readline()
                data = raw_data.decode(self.encoding, errors='ignore').strip()
                return data
        except Exception as e:
            self.logger.error(f"读取数据错误: {e}")
            
        return None
    
    def write_data(self, data: str) -> bool:
        """
        向串口写入数据
        
        Args:
            data: 要写入的数据字符串
            
        Returns:
            写入成功返回True，失败返回False
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            self.logger.error("串口未连接")
            return False
            
        try:
            # 确保数据以换行符结尾
            if not data.endswith('\n'):
                data += '\n'
                
            bytes_written = self.serial_conn.write(data.encode(self.encoding))
            self.serial_conn.flush()
            self.logger.info(f"写入数据: {data.strip()}, 字节数: {bytes_written}")
            return True
            
        except Exception as e:
            self.logger.error(f"写入数据错误: {e}")
            return False
    
    def set_data_callback(self, callback: Callable[[str, datetime], None]):
        """
        设置数据回调函数
        
        Args:
            callback: 回调函数，接收参数(data: str, timestamp: datetime)
        """
        self.data_callback = callback
    
    def _read_thread_func(self):
        """读取线程函数"""
        while self.is_reading:
            data = self.read_data()
            if data:
                timestamp = datetime.now()
                
                # 添加到缓冲区
                self.data_buffer.append({
                    'timestamp': timestamp,
                    'data': data
                })
                
                # 调用回调函数
                if self.data_callback:
                    self.data_callback(data, timestamp)
                    
                self.logger.info(f"接收数据: {data}")
            
            time.sleep(0.01)  # 避免CPU占用过高
    
    def start_reading(self) -> bool:
        """
        开始连续读取数据
        
        Returns:
            启动成功返回True，失败返回False
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            self.logger.error("串口未连接，无法开始读取")
            return False
            
        if self.is_reading:
            self.logger.warning("已经在读取数据中")
            return True
            
        self.is_reading = True
        self.read_thread = threading.Thread(target=self._read_thread_func, daemon=True)
        self.read_thread.start()
        
        self.logger.info("开始连续读取串口数据")
        return True
    
    def stop_reading(self):
        """停止连续读取数据"""
        if self.is_reading:
            self.is_reading = False
            if self.read_thread:
                self.read_thread.join(timeout=2.0)
            self.logger.info("停止读取串口数据")
    
    def get_data_buffer(self) -> List[Dict]:
        """获取数据缓冲区内容"""
        return self.data_buffer.copy()
    
    def clear_data_buffer(self):
        """清空数据缓冲区"""
        self.data_buffer.clear()
        self.logger.info("数据缓冲区已清空")
    
    def save_data_to_csv(self, filename: str = None):
        """
        将缓冲区数据保存到CSV文件
        
        Args:
            filename: 文件名，如果为None则使用时间戳命名
        """
        if not self.data_buffer:
            self.logger.warning("数据缓冲区为空，无数据可保存")
            return
            
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"serial_data_{timestamp}.csv"
            
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['timestamp', 'data']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                writer.writeheader()
                for item in self.data_buffer:
                    writer.writerow({
                        'timestamp': item['timestamp'].strftime("%Y-%m-%d %H:%M:%S.%f"),
                        'data': item['data']
                    })
                    
            self.logger.info(f"数据已保存到文件: {filename}")
            
        except Exception as e:
            self.logger.error(f"保存数据到CSV文件失败: {e}")
    
    def save_data_to_json(self, filename: str = None):
        """
        将缓冲区数据保存到JSON文件
        
        Args:
            filename: 文件名，如果为None则使用时间戳命名
        """
        if not self.data_buffer:
            self.logger.warning("数据缓冲区为空，无数据可保存")
            return
            
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"serial_data_{timestamp}.json"
            
        try:
            # 转换数据格式以便JSON序列化
            json_data = []
            for item in self.data_buffer:
                json_data.append({
                    'timestamp': item['timestamp'].strftime("%Y-%m-%d %H:%M:%S.%f"),
                    'data': item['data']
                })
                
            with open(filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(json_data, jsonfile, ensure_ascii=False, indent=2)
                
            self.logger.info(f"数据已保存到文件: {filename}")
            
        except Exception as e:
            self.logger.error(f"保存数据到JSON文件失败: {e}")


def data_received_callback(data: str, timestamp: datetime):
    """数据接收回调函数示例"""
    print(f"[{timestamp.strftime('%H:%M:%S.%f')[:-3]}] 收到数据: {data}")


def interactive_mode():
    """交互模式"""
    print("=== 串口数据读取器 - 交互模式 ===")
    
    reader = SerialDataReader()
    
    # 显示可用串口
    reader.print_available_ports()
    
    # 选择串口
    ports = reader.scan_ports()
    if not ports:
        print("未发现可用串口，程序退出")
        return
        
    while True:
        try:
            choice = input(f"\n请选择串口 (1-{len(ports)}) 或输入 'q' 退出: ").strip()
            if choice.lower() == 'q':
                return
                
            port_index = int(choice) - 1
            if 0 <= port_index < len(ports):
                selected_port = ports[port_index]['device']
                break
            else:
                print("无效选择，请重新输入")
        except ValueError:
            print("请输入有效数字")
    
    # 设置波特率
    while True:
        try:
            baudrate = input("请输入波特率 (默认9600): ").strip()
            if not baudrate:
                baudrate = 9600
            else:
                baudrate = int(baudrate)
            break
        except ValueError:
            print("请输入有效的波特率")
    
    # 连接串口
    reader.baudrate = baudrate
    if not reader.connect(selected_port):
        print("连接失败，程序退出")
        return
    
    # 设置数据回调
    reader.set_data_callback(data_received_callback)
    
    # 开始读取
    if not reader.start_reading():
        print("启动读取失败，程序退出")
        reader.disconnect()
        return
    
    print("\n开始读取数据，按 Ctrl+C 停止...")
    print("可用命令:")
    print("  's <数据>' - 发送数据")
    print("  'save csv' - 保存数据到CSV文件")
    print("  'save json' - 保存数据到JSON文件")
    print("  'clear' - 清空数据缓冲区")
    print("  'quit' - 退出程序")
    
    try:
        while True:
            command = input().strip()
            
            if command.lower() == 'quit':
                break
            elif command.startswith('s '):
                data_to_send = command[2:]
                reader.write_data(data_to_send)
            elif command.lower() == 'save csv':
                reader.save_data_to_csv()
            elif command.lower() == 'save json':
                reader.save_data_to_json()
            elif command.lower() == 'clear':
                reader.clear_data_buffer()
                print("数据缓冲区已清空")
            elif command:
                print("未知命令")
                
    except KeyboardInterrupt:
        print("\n\n收到中断信号，正在退出...")
    finally:
        reader.disconnect()
        print("程序已退出")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='串口数据读取器')
    parser.add_argument('-p', '--port', help='串口名称')
    parser.add_argument('-b', '--baudrate', type=int, default=9600, help='波特率 (默认: 9600)')
    parser.add_argument('-t', '--timeout', type=float, default=1.0, help='超时时间 (默认: 1.0)')
    parser.add_argument('-l', '--list', action='store_true', help='列出可用串口')
    parser.add_argument('-i', '--interactive', action='store_true', help='交互模式')
    parser.add_argument('-d', '--duration', type=int, help='读取持续时间（秒）')
    parser.add_argument('--csv', help='保存数据到CSV文件')
    parser.add_argument('--json', help='保存数据到JSON文件')
    
    args = parser.parse_args()
    
    # 列出可用串口
    if args.list:
        reader = SerialDataReader()
        reader.print_available_ports()
        return
    
    # 交互模式
    if args.interactive:
        interactive_mode()
        return
    
    # 命令行模式
    if not args.port:
        print("请指定串口名称，使用 -l 参数查看可用串口")
        return
    
    reader = SerialDataReader(
        port=args.port,
        baudrate=args.baudrate,
        timeout=args.timeout
    )
    
    # 连接串口
    if not reader.connect():
        print("连接失败")
        return
    
    # 设置数据回调
    reader.set_data_callback(data_received_callback)
    
    # 开始读取
    if not reader.start_reading():
        print("启动读取失败")
        reader.disconnect()
        return
    
    try:
        if args.duration:
            print(f"读取数据 {args.duration} 秒...")
            time.sleep(args.duration)
        else:
            print("开始读取数据，按 Ctrl+C 停止...")
            while True:
                time.sleep(1)
                
    except KeyboardInterrupt:
        print("\n收到中断信号，正在退出...")
    finally:
        reader.stop_reading()
        
        # 保存数据
        if args.csv:
            reader.save_data_to_csv(args.csv)
        if args.json:
            reader.save_data_to_json(args.json)
            
        reader.disconnect()
        print("程序已退出")


if __name__ == "__main__":
    main()