### 第二部分：MySQL 数据库权限
**目标**：回收 root，建立只读号，建立应用号。

登录 MySQL：`mysql -u root -p`

#### 1. 修改 Root 密码（收权）
```sql
-- MySQL 5.7
SET PASSWORD FOR 'root'@'localhost' = PASSWORD('新密码_只有管理者知道');

-- MySQL 8.0+
ALTER USER 'root'@'localhost' IDENTIFIED BY '新密码_只有管理者知道';

-- 刷新权限
FLUSH PRIVILEGES;
```

#### 2. 创建全员通用“只读账号” (发给同事)
```sql
-- 1. 创建用户 (允许从任意 IP 连接，即 '%')
CREATE USER 'team_reader'@'%' IDENTIFIED BY 'Team_Read_Pass_123';

-- 2. 授予 SELECT (查询) 和 SHOW VIEW (看视图) 权限
-- *.* 代表所有库所有表，也可以指定具体库如 app_db.*
GRANT SELECT, SHOW VIEW ON *.* TO 'team_reader'@'%';

-- 3. 刷新生效
FLUSH PRIVILEGES;
```
*验证方式*：用这个号登录，试着执行 `DELETE FROM table;`，系统应报错 `Access denied`。

#### 3. 创建“应用程序账号” (配置到代码里)
不要在代码里用 root！
```sql
-- 创建应用专用账号
CREATE USER 'app_writer'@'%' IDENTIFIED BY 'App_Secure_Pass_999';

-- 授予增删改查权限 (注意：通常不给 DROP 和 TRUNCATE 权限，防止代码Bug删表)
GRANT SELECT, INSERT, UPDATE, DELETE ON 你的业务库名.* TO 'app_writer'@'%';

FLUSH PRIVILEGES;
```
