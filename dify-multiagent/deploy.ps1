# Deploy the multi-agent Dify workflow: generate DSL -> import -> publish.
#
# Prereq: a console access token must already exist in the api container at
#   /tmp/tok/dify_access.txt and /tmp/tok/dify_csrf.txt
# (see README.md "控制台凭据" for how they are obtained).
#
# Usage:
#   pwsh -File deploy.ps1                 # update the existing app
#   pwsh -File deploy.ps1 -AppId <uuid>   # deploy to a specific app id

param(
    [string]$AppId = "04e56a64-b60c-4cac-a706-18e354af411e",
    [string]$Container = "docker-api-1",
    [string]$TokDir = "/tmp/tok"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path $here ".build"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

Write-Host "[1/4] generate DSL" -ForegroundColor Cyan
python (Join-Path $here "gen_dify_dsl.py") (Join-Path $tmp "dsl.yml")

Write-Host "[2/4] build import body" -ForegroundColor Cyan
python -c @"
import json, sys
y = open(sys.argv[1], encoding='utf-8').read()
json.dump({'mode': 'yaml-content', 'yaml_content': y, 'app_id': sys.argv[3]},
          open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False)
"@ (Join-Path $tmp "dsl.yml") (Join-Path $tmp "body.json") $AppId

Write-Host "[3/4] import" -ForegroundColor Cyan
docker cp (Join-Path $tmp "body.json") "${Container}:${TokDir}/body.json"
docker exec -e "DIFY_TOK_DIR=$TokDir" $Container python "$TokDir/dify_client.py" POST /console/api/apps/imports "$TokDir/body.json"

Write-Host "[4/4] publish" -ForegroundColor Cyan
docker cp (Join-Path $here "empty.json") "${Container}:${TokDir}/empty.json"
docker exec -e "DIFY_TOK_DIR=$TokDir" $Container python "$TokDir/dify_client.py" POST "/console/api/apps/$AppId/workflows/publish" "$TokDir/empty.json"

Write-Host "done. run: python run_e2e.py <api-key>" -ForegroundColor Green
