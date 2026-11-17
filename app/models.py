"""数据模型。

设计上有一条贯穿全局的约束：**生成内容只能引用这里标了 verified 的字段**。
`ProfileItem.verified` 与 `ProfileItem.displayable` 不是装饰性字段，
它们直接决定某个素材能不能被写进招呼语或简历（见 greeter / resume_docx）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now()


class Profile(Base):
    """用户档案。单用户应用，实际只有一条记录，但仍按表建模以便日后多份档案。"""

    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    birth: Mapped[str] = mapped_column(String(16), default="")          # YYYY-MM
    ethnicity: Mapped[str] = mapped_column(String(16), default="")
    hometown: Mapped[str] = mapped_column(String(64), default="")
    city: Mapped[str] = mapped_column(String(64), default="")           # 现居/求职地
    politics: Mapped[str] = mapped_column(String(16), default="")

    school: Mapped[str] = mapped_column(String(128), default="")
    college: Mapped[str] = mapped_column(String(128), default="")
    major: Mapped[str] = mapped_column(String(128), default="")
    degree: Mapped[str] = mapped_column(String(32), default="")
    edu_start: Mapped[str] = mapped_column(String(16), default="")      # YYYY-MM
    edu_end: Mapped[str] = mapped_column(String(16), default="")

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    items: Mapped[list["ProfileItem"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )
    preference: Mapped["Preference | None"] = relationship(
        back_populates="profile", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )


class ProfileItem(Base):
    """档案里的可引用素材：课程 / 实习 / 项目 / 技能 / 证书。

    type=project 时 content_json 建议三段：{"pain": 痛点, "approach": 做法, "result": 量化结果}
    这三段是后面生成招呼语时最好用的部分——有痛点有结果，才不像背模板。
    """

    __tablename__ = "profile_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(24), index=True)           # course/internship/project/skill/cert
    title: Mapped[str] = mapped_column(String(200), default="")
    org: Mapped[str] = mapped_column(String(200), default="")
    start_date: Mapped[str] = mapped_column(String(16), default="")
    end_date: Mapped[str] = mapped_column(String(16), default="")
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # verified=False 的素材一律不得进入生成内容——这是防编造的第一道闸门
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # displayable=False 表示可写进简历但不写进招呼语（例如过于细碎的课程）
    displayable: Mapped[bool] = mapped_column(Boolean, default=True)

    profile: Mapped[Profile] = relationship(back_populates="items")


class Preference(Base):
    """求职偏好。硬约束用于过滤，软偏好用于打分。"""

    __tablename__ = "preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"))

    cities_json: Mapped[list] = mapped_column(JSON, default=list)        # 按优先级排列
    must_weekend_off: Mapped[bool] = mapped_column(Boolean, default=True)
    must_remote_interview: Mapped[bool] = mapped_column(Boolean, default=True)
    must_certificate: Mapped[bool] = mapped_column(Boolean, default=True)

    available_from: Mapped[str] = mapped_column(String(16), default="")            # YYYY-MM-DD
    available_to: Mapped[str] = mapped_column(String(16), default="")              # 不能远程时
    remote_available_to: Mapped[str] = mapped_column(String(16), default="")       # 可远程时

    min_salary: Mapped[int] = mapped_column(Integer, default=0)          # 元/天 或 折算日薪
    direction_weights_json: Mapped[dict] = mapped_column(JSON, default=dict)
    exclude_keywords_json: Mapped[list] = mapped_column(JSON, default=list)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    profile: Mapped[Profile] = relationship(back_populates="preference")


class Job(Base):
    """抓取或手工录入的职位。status 是投递漏斗的状态机。"""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    city: Mapped[str] = mapped_column(String(64), default="")
    district: Mapped[str] = mapped_column(String(64), default="")

    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_unit: Mapped[str] = mapped_column(String(16), default="day")   # day/month/year

    exp_req: Mapped[str] = mapped_column(String(64), default="")
    edu_req: Mapped[str] = mapped_column(String(64), default="")
    days_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    months_req: Mapped[int | None] = mapped_column(Integer, nullable=True)

    jd_text: Mapped[str] = mapped_column(Text, default="")
    hr_name: Mapped[str] = mapped_column(String(64), default="")
    hr_title: Mapped[str] = mapped_column(String(64), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(32), default="manual")      # manual/boss/demo
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # new → scored → greeted → replied → interview → offer / rejected / ghosted
    # blocked 表示硬约束不满足。合法转移见 pipeline.TRANSITIONS，不要在别处硬写字符串。
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)

    # 从 HR 回复里采信的事实。**只有人点了「采信」才写进来**，不自动生效。
    # 结构：{"days_per_week": {"value": 5, "quote": "我们是双休", "message_id": 12, "at": "..."}}
    # 为什么不直接改 jd_text：JD 原文是证据，改了就再也说不清「当时写的是什么」。
    # 为什么不在打分时现算：抽取是纯函数（可复现），但**采信是判断**，判断必须留痕。
    hr_facts_json: Mapped[dict] = mapped_column(JSON, default=dict)

    matches: Mapped[list["Match"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )
    greetings: Mapped[list["Greeting"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin",
        order_by="Review.id",
    )
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin",
        order_by="Alert.id",
    )

    @property
    def latest_match(self) -> "Match | None":
        return self.matches[-1] if self.matches else None

    @property
    def latest_review(self) -> "Review | None":
        return self.reviews[-1] if self.reviews else None


class Match(Base):
    """一次匹配评估的结果。保留分项分数，是为了让「为什么 62 分」可追问。"""

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    dimension_scores_json: Mapped[dict] = mapped_column(JSON, default=dict)
    blocked_reason: Mapped[str] = mapped_column(String(400), default="")
    reason_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    job: Mapped[Job] = relationship(back_populates="matches")

    @property
    def blocked(self) -> bool:
        """是否被硬约束阻断。

        由 blocked_reason 派生而不是单独存布尔值——两个字段容易写岔，
        一旦出现「blocked=True 但 reason 为空」，界面就会显示一个没有理由的阻断。
        """
        return bool(self.blocked_reason)


class Greeting(Base):
    """招呼语。is_sent 与 confirmed_by_user 分离，是为了留住「生成过但没发」的记录。"""

    __tablename__ = "greetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    variant: Mapped[int] = mapped_column(Integer, default=1)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    job: Mapped[Job] = relationship(back_populates="greetings")


class Review(Base):
    """一次人工判断。规则之外的第二类判断来源，优先级高于规则。

    只记「人做了什么决定」，不记「决定之后应该多少分」——分数永远由
    score_only() 现算。如果这里也存一份分数，人工判断和规则升级会各自
    留一份过期快照，谁对谁错就说不清了。

    rule_ids_json 只在 decision=pass 时有值：记的是**当时命中、被推翻的那几条规则**。
    按规则 id 生效而不是给职位发一张免死金牌，是为了让规则以后升级出的新命中
    照样拦得住——一次「我认了」不该把后面所有问题一起放过。
    """

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    # pass 改判可投 / block 人工判定不投 / ack 认可规则 / wait 已去问、等回复 / reset 撤销
    decision: Mapped[str] = mapped_column(String(16), default="ack", index=True)
    rule_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    bucket: Mapped[str] = mapped_column(String(16), default="")   # 当时为什么进队列
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    job: Mapped[Job] = relationship(back_populates="reviews")


class Message(Base):
    """会话消息。direction: out=我发的，in=对方发的。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(8), default="out")
    content: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_msg_id: Mapped[str] = mapped_column(String(128), default="")


class Alert(Base):
    """一条「现在轮到你动手了」的提醒。

    它和 /inbox 里的分组不是一回事，别把两者合并：

        分组是**此刻的事实**——这条岗位现在卡在谁手上，随时都在。
        提醒是**一次事件**——18 小时前提醒你催一次，你催过了它就该消失。

    混在一起的后果是可预料的：同一句「该催了」在页面上挂三天，你学会无视这一栏；
    等它真的凉了，你也不会再多看一眼。提醒系统的失效方式从来不是「没弹出来」，
    而是**弹了太多次同一件事**。

    所以我存三样：为什么提（kind）、什么时候提的（created_at）、你什么时候说「知道了」
    （ack_at / snooze_until）。
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    # chase=该催一次 / cold=久无回音该放弃 / overdue=对方回了你还没回
    kind: Mapped[str] = mapped_column(String(16), default="chase", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ack_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 「知道了」不是永久静音。到期后同一条还能再提——否则点一次就再收不到，
    # 比没有提醒更糟：你以为什么都不缺，其实是全被自己关掉了。
    snooze_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    job: Mapped[Job] = relationship(back_populates="alerts")


class Resume(Base):
    """每次导出的简历版本。used_items_json 记录用了哪些素材，便于回溯。"""

    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    file_path: Mapped[str] = mapped_column(String(512), default="")
    md_text: Mapped[str] = mapped_column(Text, default="")
    used_items_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Run(Base):
    """抓取/生成任务的执行记录。出了问题先看这张表。"""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), default="")
    params_json: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
