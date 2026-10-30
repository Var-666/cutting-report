import type { Ref } from 'vue'
import type { QueryTask, QueryPreview, SqlValidation } from '../types/designer'
import { ref } from 'vue'
import { previewDesignerQuery, validateDesignerSql } from '../api/designer'
import { toError } from '../utils/errors'

export function useDesignerQueryPreview({ queryDraft, adminToken, today, error, notice }: { queryDraft: Ref<QueryTask | null>; adminToken: Ref<string>; today: string; error: Ref<string>; notice: Ref<string> }) {
  const queryValidation = ref<SqlValidation | null>(null)
  const queryPreview = ref<QueryPreview | null>(null)
  async function validateQuery() {
    if (!queryDraft.value) return
    error.value = ''; notice.value = ''; queryValidation.value = null
    try {
      queryValidation.value = await validateDesignerSql(queryDraft.value.sql_text, adminToken.value)
      queryDraft.value.sql_text = queryValidation.value.formatted_sql
      notice.value = `SQL只读校验通过，识别到 ${queryValidation.value.tables.length} 个数据表`
    } catch (caught) {
      const exception = toError(caught)
      error.value = exception.message }
  }
  async function previewQuery() {
    if (!queryDraft.value) return
    if (!queryDraft.value.query_task_id) { error.value = '请先保存查询任务，再执行数据预览'; return }
    error.value = ''; queryPreview.value = null
    try {
      queryPreview.value = await previewDesignerQuery(queryDraft.value.query_task_id, { report_date: today, project_code: null, ship_no: null, parameters: {}, max_rows: 10 }, adminToken.value)
    } catch (caught) {
      const exception = toError(caught)
      error.value = exception.message }
  }

  return { queryValidation, queryPreview, validateQuery, previewQuery }
}
