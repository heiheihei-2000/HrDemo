"""Generate a fake BOSS 直聘 export + resume attachments + JDs under mock_data/."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook  # noqa: E402

from hrdemo.config import JOBS_PATH, MOCK_DIR, RESUME_DIR  # noqa: E402

JOBS = {
    "Java后端开发工程师": {
        "department": "技术部",
        "salary_range": "20-30K",
        "requirements": [
            "本科及以上学历，3年以上 Java 后端开发经验",
            "熟练掌握 Spring Boot / Spring Cloud 微服务体系",
            "熟悉 MySQL 调优、Redis、Kafka 等中间件",
            "有高并发、分布式系统设计经验者优先",
            "良好的沟通能力和团队协作意识",
        ],
    },
    "产品经理": {
        "department": "产品部",
        "salary_range": "18-28K",
        "requirements": [
            "本科及以上学历，3年以上 B 端 / SaaS 产品经验",
            "具备需求分析、流程梳理、原型设计能力",
            "能使用 SQL 做基础数据分析",
            "跨部门沟通推动能力强",
            "有 HR SaaS 或招聘系统产品经验者优先",
        ],
    },
}

# (投递编号, 姓名, 性别, 年龄, 学历, 毕业院校, 工作年限, 当前公司, 当前职位, 投递职位, 期望薪资, 联系电话, 投递时间, 简历正文)
CANDIDATES = [
    ("BZ20261127001", "张伟", "男", 29, "本科", "武汉大学", "5年", "某头部电商公司", "高级Java工程师", "Java后端开发工程师", "26-30K", "138****2231", "2026-11-25 09:12", """
工作经历：
2021.07-至今 某头部电商公司 高级Java工程师
- 负责订单与库存中心微服务（Spring Cloud Alibaba），日均订单 300 万
- 主导大促秒杀链路改造，Redis 预扣库存 + Kafka 异步落库，峰值 QPS 8 万
- 牵头 MySQL 慢查询治理，核心接口 P99 从 800ms 降到 120ms
技能：Java、Spring Boot/Cloud、MySQL、Redis、Kafka、Elasticsearch、Docker/K8s
自我评价：对高并发场景有实战经验，带过 4 人小组。"""),
    ("BZ20261127002", "李娜", "女", 27, "硕士", "华中科技大学", "3年", "某金融科技公司", "Java开发工程师", "Java后端开发工程师", "22-26K", "139****5562", "2026-11-25 14:40", """
工作经历：
2023.07-至今 某金融科技公司 Java开发工程师
- 参与支付清结算系统开发，基于 Spring Boot + MyBatis
- 负责对账模块重构，引入 Redis 缓存与分库分表（ShardingSphere）
- 编写单元测试覆盖率提升到 75%
技能：Java、Spring Boot、MySQL、Redis、RabbitMQ、Linux
自我评价：学习能力强，代码规范，有金融级系统稳定性意识。"""),
    ("BZ20261127003", "王强", "男", 35, "大专", "某职业技术学院", "10年", "某软件外包公司", "Java开发", "Java后端开发工程师", "33-38K", "137****8890", "2026-11-26 10:05", """
工作经历：
2015.03-至今 某软件外包公司 Java开发
- 维护政府/国企项目的老旧系统，技术栈 SSH（Struts2 + Spring + Hibernate）
- 负责需求对接、页面开发（JSP）与数据库脚本编写（Oracle）
技能：Java、SSH、JSP、Oracle、SVN
自我评价：项目经验多，能吃苦，可接受出差。"""),
    ("BZ20261127004", "刘洋", "男", 24, "本科", "湖北工业大学", "2年", "某创业公司", "全栈开发", "Java后端开发工程师", "15-18K", "136****1024", "2026-11-26 16:21", """
工作经历：
2024.07-至今 某创业公司 全栈开发
- 使用 Spring Boot 开发小程序后端接口，Vue 开发管理后台
- 负责 MySQL 表设计与简单的 Redis 缓存
技能：Java、Spring Boot、Vue、MySQL、Redis 基础
自我评价：前后端都能做，希望在后端方向深入发展。"""),
    ("BZ20261127005", "陈静", "女", 31, "本科", "北京邮电大学", "7年", "某云服务厂商", "后端技术专家", "Java后端开发工程师", "28-32K", "135****6677", "2026-11-26 20:33", """
工作经历：
2019.07-至今 某云服务厂商 后端技术专家
- 负责消息中间件平台（基于 Kafka/RocketMQ）的研发与运维，服务 2000+ 业务方
- 设计多租户限流与熔断组件，支撑日均百亿级消息
- 主导从单体到 Spring Cloud 微服务的迁移
2017.07-2019.06 某互联网公司 Java工程师
技能：Java、Go、Spring Cloud、Kafka、RocketMQ、MySQL、Redis、K8s
自我评价：擅长分布式系统设计，有团队技术管理经验。"""),
    ("BZ20261127006", "赵磊", "男", 26, "本科", "江汉大学", "1年", "某软件公司", "测试工程师", "Java后端开发工程师", "12-15K", "133****4455", "2026-11-27 08:47", """
工作经历：
2025.07-至今 某软件公司 测试工程师
- 负责功能测试与接口自动化测试（Postman、Python）
- 业余自学 Java 与 Spring Boot，完成个人博客项目
技能：Python、Java 基础、Spring Boot 入门、MySQL 基础
自我评价：希望转型后端开发，学习意愿强。"""),
    ("BZ20261127007", "孙悦", "女", 30, "本科", "中南财经政法大学", "6年", "某 HR SaaS 公司", "高级产品经理", "产品经理", "25-28K", "158****3321", "2026-11-25 11:30", """
工作经历：
2020.07-至今 某 HR SaaS 公司 高级产品经理
- 负责招聘管理（ATS）模块，从 0 到 1 设计简历解析、面试安排、Offer 审批流程
- 推动与企业微信、钉钉的集成，客户续费率提升 18%
- 使用 SQL 搭建产品数据看板，定义漏斗转化指标
技能：Axure、墨刀、SQL、需求分析、B 端流程设计
自我评价：深度理解招聘业务，擅长跨部门推进。"""),
    ("BZ20261127008", "周杰", "男", 28, "硕士", "武汉理工大学", "4年", "某生鲜电商平台", "产品经理", "产品经理", "22-25K", "159****7788", "2026-11-26 09:58", """
工作经历：
2022.07-至今 某生鲜电商平台 产品经理
- 负责 C 端会员与营销活动产品，主导拼团、积分商城功能
- 通过 A/B 测试优化下单转化，GMV 提升 12%
技能：Figma、Axure、SQL、A/B 测试、用户研究
自我评价：数据驱动，用户体验敏感，希望向 B 端方向发展。"""),
    ("BZ20261127009", "吴敏", "女", 25, "本科", "湖北大学", "1.5年", "某教育公司", "产品助理", "产品经理", "12-15K", "150****2468", "2026-11-27 10:15", """
工作经历：
2025.06-至今 某教育公司 产品助理
- 协助产品经理整理需求文档、跟进测试与上线
- 负责用户反馈收集与分类
技能：Axure、Office、基础数据分析（Excel）
自我评价：细心负责，希望独立负责产品模块。"""),
    ("BZ20261127010", "郑浩", "男", 33, "本科", "华中师范大学", "8年", "某 CRM 软件公司", "产品专家", "产品经理", "32-35K", "186****1357", "2026-11-27 11:02", """
工作经历：
2018.07-至今 某 CRM 软件公司 产品专家
- 负责销售自动化（SFA）与客户管理产品线，管理 5 人产品团队
- 主导低代码表单与审批引擎设计，服务 3000+ 企业客户
技能：B 端架构设计、SQL、项目管理、PRD 撰写
自我评价：有完整 B 端产品线经验，关注商业化结果。"""),
]

EXPORT_HEADER = ["投递编号", "姓名", "性别", "年龄", "学历", "毕业院校", "工作年限", "当前公司", "当前职位",
                 "投递职位", "期望薪资", "联系电话", "投递时间", "沟通状态", "简历附件"]


def main() -> None:
    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_PATH.write_text(json.dumps(JOBS, ensure_ascii=False, indent=2), encoding="utf-8")

    wb = Workbook()
    ws = wb.active
    ws.title = "投递记录"
    ws.append(EXPORT_HEADER)
    for c in CANDIDATES:
        aid, name, gender, age, edu, school, years, company, title, position, salary, phone, applied, body = c
        ws.append([aid, name, gender, age, edu, school, years, company, title, position, salary, phone, applied,
                   "新投递", f"{aid}.pdf"])
        resume = (f"【简历】{name}\n性别：{gender}  年龄：{age}  学历：{edu}（{school}）\n"
                  f"工作年限：{years}  当前：{company} / {title}\n求职意向：{position}  期望薪资：{salary}\n"
                  f"联系电话：{phone}\n{body.strip()}\n")
        (RESUME_DIR / f"{aid}.txt").write_text(resume, encoding="utf-8")
    out = MOCK_DIR / "boss_export_20261127.xlsx"
    wb.save(out)
    print(f"已生成 {out.name}、{len(CANDIDATES)} 份简历、{len(JOBS)} 个岗位 JD -> {MOCK_DIR}")


if __name__ == "__main__":
    main()
