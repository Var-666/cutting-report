<script setup>
defineOptions({ name: 'CatalogTreeNode' })

const props = defineProps({
  node: { type: Object, required: true },
  selectedId: { type: String, default: '' },
  expandedIds: { type: Object, required: true },
})
const emit = defineEmits(['select', 'toggle'])

const hasChildren = () => Boolean(props.node.children?.length)
</script>

<template>
  <li class="catalog-node" role="treeitem" :aria-expanded="hasChildren() ? expandedIds.has(String(node.catalog_id)) : undefined">
    <div class="catalog-node-row" :class="{ selected: String(node.catalog_id) === selectedId }">
      <button v-if="hasChildren()" class="catalog-toggle" type="button" :aria-label="`${expandedIds.has(String(node.catalog_id)) ? '收起' : '展开'}${node.title}`" @click="emit('toggle', node)">
        <span :class="{ expanded: expandedIds.has(String(node.catalog_id)) }">›</span>
      </button>
      <span v-else class="catalog-toggle-spacer" aria-hidden="true"></span>
      <span class="catalog-folder" aria-hidden="true"></span>
      <button class="catalog-label" type="button" :title="node.title" @click="emit('select', node)">{{ node.title }}</button>
    </div>
    <ul v-if="hasChildren() && expandedIds.has(String(node.catalog_id))" class="catalog-children" role="group">
      <CatalogTreeNode
        v-for="child in node.children"
        :key="child.catalog_id"
        :node="child"
        :selected-id="selectedId"
        :expanded-ids="expandedIds"
        @select="emit('select', $event)"
        @toggle="emit('toggle', $event)"
      />
    </ul>
  </li>
</template>

<style scoped>
.catalog-node,.catalog-children { margin:0; padding:0; list-style:none; }
.catalog-children { margin-left:18px; border-left:1px solid #dbe5ea; }
.catalog-node-row { display:flex; align-items:center; min-height:34px; padding:2px 5px 2px 2px; border-radius:4px; }
.catalog-node-row:hover { background:#f0f6f8; }
.catalog-node-row.selected { background:#dff1f5; box-shadow:inset 3px 0 #1688a2; }
.catalog-toggle,.catalog-toggle-spacer { width:24px; height:28px; flex:0 0 24px; }
.catalog-toggle { display:grid; place-items:center; padding:0; border:0; color:#58707e; background:transparent; cursor:pointer; }
.catalog-toggle span { display:block; font-size:20px; line-height:1; transform:rotate(0); transition:transform .15s ease; }
.catalog-toggle span.expanded { transform:rotate(90deg); }
.catalog-folder { position:relative; width:17px; height:12px; flex:0 0 17px; margin:0 7px 0 1px; border:1px solid #69a9c0; border-radius:2px; background:#dff3f7; }
.catalog-folder::before { content:''; position:absolute; left:1px; top:-5px; width:8px; height:4px; border:1px solid #69a9c0; border-bottom:0; border-radius:2px 2px 0 0; background:#dff3f7; }
.catalog-label { min-width:0; overflow:hidden; padding:6px 2px; border:0; color:#344d5d; background:transparent; text-align:left; text-overflow:ellipsis; white-space:nowrap; cursor:pointer; font-size:12px; }
.catalog-node-row.selected .catalog-label { color:#075a79; font-weight:700; }
</style>
