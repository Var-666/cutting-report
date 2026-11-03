from tests.line_fixtures import as_line_services, runtime_line
from types import SimpleNamespace

from app.database import condition_fields, condition_query
from app.models.conditions import ConditionGroup, ConditionRule
from test_conditions import api


def test_condition_sql_uses_server_fields_and_bound_values():
    value = "part' OR 1=1 --"
    group = ConditionGroup(rules=[ConditionRule(field='document_no', operator='eq', value=value)])
    sql, parameters = condition_query.compile_conditions(group, condition_fields.fields_for('cutting_files'))
    assert sql == '(a.DocumentNo = ?)'
    assert parameters == [value]
    assert condition_query.matches_conditions({'document_no': value}, group, condition_fields.fields_for('cutting_files'))


def test_public_schema_and_mock_search_keep_their_http_contract_with_device_context(api):
    app, client, downloads = api
    app.state.lines = as_line_services({'Cutter01': SimpleNamespace(settings=app.state.cutting_dispatch.settings)})
    response = client.get('/api/v1/conditions/cutting_files/schema?device_id=Cutter01')
    assert response.status_code == 200
    schema = response.json()['data']
    assert schema['requires_filter'] and schema['max_sort_rules'] == 5
    assert all('expression' not in field for field in schema['fields'])
    project = app.state.cutting_dispatch.pdm.projects[0]['project_id']
    result = client.post('/api/v1/cutting/files/search', json={
        'filters': {'rules': [{'field': 'project_id', 'operator': 'eq', 'value': project}]},
        'page_size': 1, 'sort': [{'field': 'document_no', 'direction': 'desc'}],
    })
    assert result.status_code == 200
    assert result.json()['data']['total'] == 2
    assert len(result.json()['data']['items']) == 1
    response = client.post('/api/v1/cutting/downloads/search', json={})
    assert response.status_code == 200 and response.json()['data']['total'] == 0
    assert downloads.list(1, 10)[1] == 0
