### ⚠️ 一个必须要知道的“大坑”
**环境变量 `MONGO_INITDB_ROOT_USERNAME` 和 `PASSWORD` 是一次性的！**

*   **初次启动时**：当 `./data` 目录为空，MongoDB 会读取这两个变量，创建这个 root 账号并开启鉴权。
*   **再次启动时**：如果 `./data` 里已经有数据了，MongoDB 会**忽略**这两个变量。
*   **后果**：如果你以后想改 root 密码，**直接修改 docker-compose.yml 是没用的**，必须通过命令行进入数据库修改。

---

### 实操教程：在 Docker 环境下落地“分权管理”

结合之前的“极简管控方案”，我们可以直接在这个 Docker 容器里创建 `dev_reader`（只读）和 `app_writer`（应用）账号。

#### 1. 启动容器
如果你还没启动，先运行：
```bash
docker-compose up -d
```

#### 2. 进入 MongoDB 容器内部
你需要进入容器的命令行环境来执行管理操作：
```bash
# 假设你的目录名是 mongodb，容器名通常是 mongodb-mongo-1，或者用 docker ps 看一下 ID
docker exec -it <你的容器ID或名称> mongosh -u mongo -p <your_password> --authenticationDatabase admin
```
*注意：Mongo 6.0+ 之后默认客户端命令是 `mongosh` 而不是 `mongo`。*

#### 3. 执行分权脚本（复制粘贴即可）

进入 `mongosh` 界面（看到 `test>` 或 `admin>` 提示符）后，执行以下命令。

**假设你的业务数据库名字叫 `ai_fault_diagnosis`**（如果没有会自动创建）：

```javascript
// 1. 切换到你的业务数据库
use ai_fault_diagnosis

// 2. 创建【全员只读账号】 (发给同事)
// 只有 read 权限，绝对删不掉库
db.createUser({
  user: "team_reader",
  pwd: "Team_Reader_Pass_123",  // 请修改为更复杂的密码
  roles: [ { role: "read", db: "ai_fault_diagnosis" } ]
})

// 3. 创建【应用程序账号】 (写在代码里)
// 拥有读写权限
db.createUser({
  user: "app_writer",
  pwd: "App_Writer_Pass_999",  // 请修改为更复杂的密码
  roles: [ { role: "readWrite", db: "ai_fault_diagnosis" } ]
})
```

### 管理建议

1.  **未来修改密码**：
    如果未来你想修改 root 密码，不要改 yaml 文件，而是进入容器执行：
    ```bash
    docker exec -it <容器ID> mongosh -u mongo -p 旧密码 --authenticationDatabase admin
    ```
    ```javascript
    use admin
    db.changeUserPassword("mongo", "新密码")
    ```
