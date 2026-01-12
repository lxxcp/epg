import datetime
import time
import requests
from bs4 import BeautifulSoup as bs
from dateutil import tz
import gzip
import random
import json

# 读取配置文件
with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

# 获取所有频道信息
tvmao_all_channels = config["channels"]

# 设置请求头
headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36",
}

# 获取节目表的核心程序
def get_epg(channel_name, channel_id, dt):
    epgs = []
    msg = ""
    success = 1
    ban = 0  # 标识是否被BAN掉了
    now_date = datetime.datetime.now().date()
    need_date = dt
    delta = (need_date - now_date).days  # 获取天数差值，直接使用 delta.days

    # 计算目标日期的星期几（1=星期一，7=星期日）
    need_weekday = dt.weekday() + 1

    url = f"https://lighttv.tvmao.com/qa/qachannelschedule?epgCode={channel_id}&op=getProgramByChnid&epgName=&isNew=on&day={need_weekday}"
    
    # 调试输出
    print(f"Fetching EPG for {channel_name} (Channel ID: {channel_id}, Date: {dt}, Day: {need_weekday})")
    
    try:
        res = requests.get(url, headers=headers)
        res_j = res.json()
        
        # 调试代码：打印返回的 JSON 数据
        print(f"Response JSON for {channel_name} (Channel ID: {channel_id}, Date: {dt}):")
        print(json.dumps(res_j, indent=4, ensure_ascii=False))  # 格式化输出 JSON 数据
        
        if len(res_j) > 2 and isinstance(res_j[2], dict) and "pro" in res_j[2]:
            datas = res_j[2]["pro"]
            for i, data in enumerate(datas):
                title = data["name"]
                starttime_str = data["time"]
                starttime = datetime.datetime.combine(dt, datetime.time(int(starttime_str[:2]), int(starttime_str[-2:])))
                
                # 假设每个节目的时长为 1 小时
                if i < len(datas) - 1:
                    next_starttime_str = datas[i + 1]["time"]
                    next_starttime = datetime.datetime.combine(dt, datetime.time(int(next_starttime_str[:2]), int(next_starttime_str[-2:])))
                    endtime = next_starttime
                else:
                    # 如果是最后一个节目，假设时长为 1 小时
                    endtime = starttime + datetime.timedelta(hours=1)
                
                epg = {
                    "channel_id": channel_id,
                    "starttime": starttime,
                    "endtime": endtime,
                    "title": title,
                    "desc": "",
                    "program_date": dt,
                }
                epgs.append(epg)
        else:
            success = 0
            msg = f"spider-tvmao-No program data for {channel_name}"
    except Exception as e:
        success = 0
        msg = f"spider-tvmao-{e}"
    
    ret = {
        "success": success,
        "epgs": epgs,
        "msg": msg,
        "last_program_date": dt,
        "ban": 0,
        "source": "tvmao",
    }
    return ret

# 将 EPG 数据保存为 XML 文件
def save_epg_to_xml(all_epgs):
    xmlhead = '<?xml version="1.0" encoding="UTF-8"?><tv>\n'
    xmlbottom = "</tv>"
    tz_sh = tz.gettz("Asia/Shanghai")
    tz_str = " +0800"
    xmldir = "tvmao.xml"

    with open(xmldir, "w", encoding="utf-8") as f:
        f.write(xmlhead)
        # 写入频道信息
        for channel_name, channel_info in tvmao_all_channels.items():
            channel_id = channel_info[1]
            c = f'<channel id="{channel_id}"><display-name lang="zh">{channel_name}</display-name></channel>\n'
            f.write(c)
        # 写入节目信息
        for epg in all_epgs:
            start = epg["starttime"].astimezone(tz=tz_sh).strftime("%Y%m%d%H%M%S") + tz_str
            end = epg["endtime"].astimezone(tz=tz_sh).strftime("%Y%m%d%H%M%S") + tz_str if epg["endtime"] else start
            title = epg["title"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;")
            desc = epg["desc"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;")
            programinfo = f'<programme channel="{epg["channel_id"]}" start="{start}" stop="{end}"><title lang="zh">{title}</title></programme>\n'
            f.write(programinfo)
        f.write(xmlbottom)

    print(f"已经生成 XML 文件：{xmldir}")

    # 压缩为 gz 文件
    with open(xmldir, 'rb') as f_in:
        with gzip.open('tvmao.xml.gz', 'wb') as f_out:
            f_out.writelines(f_in)
    print("已经生成压缩文件：tvmao.xml.gz")

# 主函数
def main():
    all_epgs = []  # 存储所有频道的节目表
    today = datetime.datetime.now().date()  # 获取当前日期
    dates = [today, today + datetime.timedelta(days=1), today + datetime.timedelta(days=2)]  # 今天、明天、后天

    for dt in dates:  # 遍历今天、明天、后天的日期
        print(f"正在获取 {dt} 的节目表...")
        for channel_name, channel_info in tvmao_all_channels.items():
            channel_url_part, channel_id = channel_info
            ret = get_epg(channel_name, channel_id, dt)
            if ret["success"]:
                all_epgs.extend(ret["epgs"])
            else:
                print(f"获取 {channel_name} 的节目表失败: {ret['msg']}")
    
    # 将所有节目表保存到一个 XML 文件中
    save_epg_to_xml(all_epgs)

if __name__ == "__main__":
    main()
