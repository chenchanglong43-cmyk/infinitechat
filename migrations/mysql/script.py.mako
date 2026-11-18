## 迁移的说明文字, 生成时填进来
"""${message}

## 本次的版本号
Revision ID: ${up_revision}
## 上一个版本号
Revises: ${down_revision | comma,n}
## 生成时间
Create Date: ${create_date}
## 说明文字结束
"""

## 导入类型提示要用的 Sequence
from collections.abc import Sequence

## 导入 SQLAlchemy, 起别名 sa
import sqlalchemy as sa
## 导入 alembic 的建表改表工具 op
from alembic import op
## 自动生成迁移时需要的额外导入
${imports if imports else ""}

## 本版本号
revision: str = ${repr(up_revision)}
## 上一个版本号
down_revision: str | None = ${repr(down_revision)}
## 分支标签, 一般不用
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
## 依赖的其他版本, 一般不用
depends_on: str | Sequence[str] | None = ${repr(depends_on)}

## 升级时执行的函数
def upgrade() -> None:
    ## 自动生成的升级语句, 没有就 pass
    ${upgrades if upgrades else "pass"}

## 降级时执行的函数
def downgrade() -> None:
    ## 自动生成的降级语句, 没有就 pass
    ${downgrades if downgrades else "pass"}