import os
import gzip
import xml.etree.ElementTree as ET
import requests
import logging
from copy import deepcopy
import datetime
import pytz
from xml.sax.saxutils import escape

# 配置参数
config_file = os.path.join(os.path.dirname(__file__), 'config.txt')
output_file_gz = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'e.xml.gz')
TIMEZONE = pytz.timezone('Asia/Shanghai')

def load_config_and_alias(config_file):
    """从合并的配置文件中加载频道名称和别名映射"""
    config_names = set()
    alias_mapping = {}
    
    try:
        with open(config_file, 'r', encoding='utf-8') as file:
            for line in file:
                cleaned_line = line.strip()
                if not cleaned_line or cleaned_line.startswith('#'):
                    continue
                
                # 按逗号分割
                parts = [p.strip() for p in cleaned_line.split(',') if p.strip()]
                
                if len(parts) == 0:
                    continue
                
                # 第一个总是标准名称
                standard_name = parts[0]
                
                # 添加到配置名称集合
                config_names.add(standard_name)
                
                # 如果有别名，添加到映射表
                if len(parts) > 1:
                    for alias in parts[1:]:
                        alias_mapping[alias] = standard_name
        
        logging.info(f"Loaded {len(config_names)} channels and {len(alias_mapping)} alias mappings from {config_file}")
        return config_names, alias_mapping
        
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        return set(), {}

def map_channel(display_name, config_names, alias_mapping):
    """频道匹配核心逻辑"""
    # 1. 直接匹配config.txt
    if display_name in config_names:
        logging.info(f"直接匹配成功: {display_name}")
        return display_name
    
    # 2. 通过映射表匹配
    mapped_name = alias_mapping.get(display_name)
    if mapped_name:
        if mapped_name in config_names:
            logging.info(f"别名映射成功: {display_name} -> {mapped_name}")
            return mapped_name
        else:
            logging.warning(f"映射目标不在配置中: {mapped_name} (来自 {display_name})")
    else:
        logging.debug(f"未找到映射: {display_name}")
    
    return None

def parse_epg_time(time_str):
    """解析EPG时间并转换为本地时区"""
    try:
        dt = datetime.datetime.strptime(time_str[:14], "%Y%m%d%H%M%S")
        if time_str.endswith('Z'):
            dt = pytz.utc.localize(dt)
        else:
            dt = TIMEZONE.localize(dt)
        return dt
    except Exception as e:
        logging.warning(f"Time parse failed: {time_str} - {e}")
        return None

def process_sources(urls, alias_mapping, config_names):
    channels = {}  # 频道ID: 频道节点
    programmes = {}  # 频道ID: [节目列表]
    
    now = datetime.datetime.now(TIMEZONE)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    logging.info(f"当前时间: {now}, 今日开始时间: {today_start}")

    for url in urls:
        try:
            # 获取并解析EPG数据
            logging.info(f"Processing: {url}")
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            
            if url.endswith('.gz') or 'gzip' in response.headers.get('Content-Encoding', ''):
                content = gzip.decompress(response.content)
            else:
                content = response.content
                
            root = ET.fromstring(content)
            
            # 构建频道映射表
            channel_map = {}
            # 修改后的频道处理部分
            for channel in root.findall('channel'):
                channel_id = channel.get('id')
                # 获取display-name元素
                display_name_elem = channel.find('display-name[@lang="zh"]')
                if display_name_elem is None:
                     display_name_elem = channel.find('display-name')
                if display_name_elem is None or display_name_elem.text is None:
                     logging.debug(f"频道 {channel_id} 缺少display-name")
                     continue
                display_name = display_name_elem.text.strip()
                mapped_id = map_channel(display_name, config_names, alias_mapping)
                if mapped_id:
                    channel_map[channel_id] = mapped_id
                    if mapped_id not in channels:
                        new_channel = deepcopy(channel)
                        new_channel.set('id', mapped_id)
                        # 清理旧display-name
                        for dn in new_channel.findall('display-name'):
                            new_channel.remove(dn)
                        ET.SubElement(new_channel, 'display-name', {'lang': 'zh'}).text = mapped_id
                        channels[mapped_id] = new_channel
                        logging.info(f"添加频道: {mapped_id} (原名称: {display_name})")
            
            # 处理节目信息
            for programme in root.findall('programme'):
                original_id = programme.get('channel')
                mapped_id = channel_map.get(original_id)
                if not mapped_id:
                    continue
                
                # 时间过滤
                start_time = parse_epg_time(programme.get('start'))
                if not start_time or start_time < today_start:
                    continue
                
                # 克隆并更新节目信息
                new_prog = deepcopy(programme)
                for elem in new_prog.iter():
                    if elem.text:
                        elem.text = escape(elem.text)
                    if elem.tail:
                        elem.tail = escape(elem.tail)
                new_prog.set('channel', mapped_id)
                new_prog.set('start', start_time.strftime("%Y%m%d%H%M%S +0800"))
                if programme.get('stop'):
                    stop_time = parse_epg_time(programme.get('stop'))
                    if stop_time:
                        new_prog.set('stop', stop_time.strftime("%Y%m%d%H%M%S +0800"))
                
                programmes.setdefault(mapped_id, []).append(new_prog)
                
            logging.info(f"Processed: {len(channel_map)} channels, {len(programmes)} programmes from {url}")
            
        except Exception as e:
            logging.error(f"Failed to process {url}: {e}")

    # 生成最终XML
    root = ET.Element('tv')
    # 添加频道
    for channel in channels.values():
        root.append(deepcopy(channel))
    # 添加节目（带去重）
    total_progs = 0
    for channel_id, progs in programmes.items():
        seen = set()
        unique_progs = []
        for p in progs:
            key = f"{p.get('start')}|{p.find('title').text if p.find('title') else ''}"
            if key not in seen:
                seen.add(key)
                unique_progs.append(p)
        total_progs += len(unique_progs)
        root.extend(unique_progs)
    
    # 保存压缩文件
    xml_str = ET.tostring(root, encoding='utf-8')
    try:
        with gzip.open(output_file_gz, 'wb') as f:
            f.write(b'<?xml version="1.0" encoding="utf-8"?>\n')
            f.write(xml_str)
        logging.info(f"EPG generated: {len(channels)} channels, {total_progs} programmes")
    except Exception as e:
        logging.error(f"Failed to save EPG: {e}")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 初始化配置（从合并的文件中加载）
    config_names, alias_mapping = load_config_and_alias(config_file)
    
    # 数据源列表
    epg_urls = [
        'https://raw.githubusercontent.com/lxxcp/epg/main/cntvepg.xml.gz',
        'https://raw.githubusercontent.com/lxxcp/epg/main/tvmao.xml.gz',
        'https://raw.githubusercontent.com/fanmingming/live/refs/heads/main/e.xml',
        'https://github.com/plsy1/epg/blob/main/e/seven-days.xml.gz',
        'https://github.com/Li-Xingyu/ZJ_IPTV_EPG/blob/main/epg.xml',
        'https://github.com/zzq1234567890/epg/raw/refs/heads/main/swepg.xml.gz',
        'https://epg.zsdc.eu.org/t.xml',
        'https://raw.githubusercontent.com/mytv-android/myEPG/master/output/epg.gz',
        'https://epg.pw/xmltv/epg_CN.xml.gz',
        'https://epg.pw/xmltv/epg_TW.xml.gz',
        'https://epg.pw/xmltv/epg_HK.xml.gz',
        'http://liliu.serv00.net/epg/all.xml.gz',
        'https://raw.githubusercontent.com/plsy1/epg/main/e/seven-days.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/erw.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/112114.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/51zmt.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/epgpw_cn.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/epgpw_hk.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/epgpw_tw.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/bjiptv.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/chuanliu.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/cqcu.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/cqiptv.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/cqlaidian.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/fjyd.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/migu.xml.gz',
        'https://github.com/peterHchina/iptv/blob/main/EPG.xml',
    ]
    
    process_sources(epg_urls, alias_mapping, config_names)