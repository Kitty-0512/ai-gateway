-- AI 网关元数据表 — 在 TiDB Cloud 中手动执行一次即可
-- 注意：用户名前缀已包含在连接字符串中，直接 COPY-PASTE 执行即可

CREATE TABLE IF NOT EXISTS `datasets` (
    `id`         BIGINT AUTO_INCREMENT PRIMARY KEY,
    `file_name`  VARCHAR(255)  NOT NULL COMMENT '原始文件名',
    `file_path`  VARCHAR(500)  NOT NULL COMMENT '上传路径',
    `file_size`  BIGINT        NOT NULL DEFAULT 0 COMMENT '文件字节数',
    `table_name` VARCHAR(128)  NOT NULL COMMENT '对应数据表名',
    `schema_json` TEXT         COMMENT '字段定义 JSON',
    `row_count`  INT           NOT NULL DEFAULT 0 COMMENT '行数',
    `file_type`  VARCHAR(20)   NOT NULL COMMENT 'csv / xlsx / xls',
    `uploaded_by` INT          NOT NULL DEFAULT 1,
    `status`     INT           NOT NULL DEFAULT 1 COMMENT '1-正常 0-删除',
    `created_at` DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `conversations` (
    `id`          BIGINT AUTO_INCREMENT PRIMARY KEY,
    `title`       VARCHAR(200)  NOT NULL DEFAULT '新对话',
    `user_id`     INT           NOT NULL DEFAULT 1,
    `dataset_ids` TEXT          COMMENT '关联数据集 ID JSON 数组',
    `status`      INT           NOT NULL DEFAULT 1 COMMENT '1-正常',
    `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `messages` (
    `id`              BIGINT AUTO_INCREMENT PRIMARY KEY,
    `conversation_id` BIGINT        NOT NULL,
    `role`            VARCHAR(50)   NOT NULL COMMENT 'user / assistant',
    `content`         TEXT          NOT NULL,
    `sql_generated`   TEXT          COMMENT 'LLM 生成的 SQL',
    `sql_result`      TEXT          COMMENT 'SQL 执行结果 JSON',
    `chart_config`    TEXT          COMMENT '图表配置 JSON',
    `chart_type`      VARCHAR(50)   COMMENT 'bar / line / pie / table',
    `token_usage`     INT           NOT NULL DEFAULT 0,
    `status`          INT           NOT NULL DEFAULT 1 COMMENT '1-正常',
    `created_at`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
