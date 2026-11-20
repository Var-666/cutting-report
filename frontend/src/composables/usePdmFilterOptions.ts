import { computed, shallowRef, watch } from 'vue'
import type { Ref } from 'vue'
import { getPdmOptions } from '../api/pdmStatistics'
import type { PdmBranchOptions } from '../api/generated/types.gen'
import { pollReportUpdates } from '../api/reportRequests'
import { clonePdmFilters } from '../utils/pdmFilters'
import type { PdmDocumentKind, PdmFilterOptions, PdmFilters } from '../utils/pdmFilters'

export function usePdmFilterOptions(draft: Ref<PdmFilters>, visible: Ref<boolean>, kind: PdmDocumentKind) {
  const lastOptions = shallowRef<{ scope: string; value: PdmBranchOptions }>()
  const status = shallowRef<'loading' | 'ready' | 'error'>('loading')
  const error = shallowRef('')
  const revision = shallowRef(0)
  let previousScope: string | undefined
  let previousRevision = 0
  const scope = () => {
    const branch = draft.value[kind]
    return JSON.stringify([branch.types, branch.projects, branch.professions])
  }
  const options = computed<PdmFilterOptions>(() => lastOptions.value?.scope === scope()
    ? { [kind]: lastOptions.value.value } : {})
  watch(() => JSON.stringify([visible.value, scope(), revision.value]), async (_, __, onCleanup) => {
    if (!visible.value) { previousScope = undefined; return }
    const controller = new AbortController()
    let active = true
    onCleanup(() => { active = false; controller.abort() })
    status.value = 'loading'
    error.value = ''
    const requestedScope = scope()
    const mode = revision.value !== previousRevision ? 'refresh' : 'read'
    previousRevision = revision.value
    try {
      const submitted = clonePdmFilters(draft.value)[kind]
      function apply(result: PdmBranchOptions) {
        if (!active) return
        const next = clonePdmFilters(draft.value)
        const branch = next[kind]
        branch.projects = branch.projects.filter(id => result.projects?.some(option => option.value === id))
        branch.professions = branch.professions.filter(id => result.professions?.some(option => option.value === id))
        if (previousScope === requestedScope && branch.responsiblePeople !== null) {
          const people = new Set(result.responsible_people?.map(option => option.value))
          branch.responsiblePeople = branch.responsiblePeople.filter(value => people.has(value))
        } else {
          branch.responsiblePeople = null
        }
        previousScope = requestedScope
        lastOptions.value = { scope: requestedScope, value: result }
        draft.value = next
        status.value = 'ready'
      }
      const result = await getPdmOptions(kind, submitted, controller.signal, { mode })
      apply(result)
      await pollReportUpdates(result, () => getPdmOptions(kind, submitted, controller.signal, { mode: 'poll' }), apply, controller.signal)
    } catch (caught) {
      if (!active) return
      error.value = caught instanceof Error ? caught.message : String(caught)
      status.value = 'error'
    }
  }, { immediate: true })
  return { options, status, error, retry: () => { revision.value++ } }
}
