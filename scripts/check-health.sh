#!/bin/bash
# Health Check and Diagnostics Script

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Health Check Diagnostics${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Ensure clean environment
unset AWS_ENDPOINT_URL
unset LOCALSTACK_ENDPOINT_URL

if [ -z "$AWS_PROFILE" ]; then
    export AWS_PROFILE=uic
fi

cd terraform
ECS_CLUSTER=$(terraform output -raw ecs_cluster_name)
ECS_SERVICE=$(terraform output -raw ecs_service_name)
ALB_URL=$(terraform output -raw alb_url)
cd ..

echo -e "${BLUE}1. Checking ECS Service Status${NC}"
aws ecs describe-services \
    --cluster ${ECS_CLUSTER} \
    --services ${ECS_SERVICE} \
    --region us-east-1 \
    --query 'services[0].{DesiredCount:desiredCount,RunningCount:runningCount,Status:status,Deployments:deployments[*].{Status:status,Running:runningCount,Desired:desiredCount,RolloutState:rolloutState}}' \
    --output json

echo ""
echo -e "${BLUE}2. Checking Task Details${NC}"
TASK_ARNS=$(aws ecs list-tasks \
    --cluster ${ECS_CLUSTER} \
    --service-name ${ECS_SERVICE} \
    --region us-east-1 \
    --query 'taskArns[*]' \
    --output text)

if [ -z "$TASK_ARNS" ]; then
    echo -e "${RED}No tasks running!${NC}"
else
    echo "Tasks: $TASK_ARNS"
    echo ""

    # Get first task ARN for detailed check
    FIRST_TASK=$(echo $TASK_ARNS | awk '{print $1}')
    echo -e "${BLUE}3. Checking Task Health (first task)${NC}"
    aws ecs describe-tasks \
        --cluster ${ECS_CLUSTER} \
        --tasks ${FIRST_TASK} \
        --region us-east-1 \
        --query 'tasks[0].{LastStatus:lastStatus,HealthStatus:healthStatus,StoppedReason:stoppedReason,Containers:containers[*].{Name:name,Status:lastStatus,Health:healthStatus}}' \
        --output json
fi

echo ""
echo -e "${BLUE}4. Recent CloudWatch Logs (last 5 minutes)${NC}"
aws logs tail /ecs/equalify-pdf \
    --since 5m \
    --region us-east-1 \
    --format short 2>/dev/null | tail -30

echo ""
echo -e "${BLUE}5. Testing Health Endpoint${NC}"
echo "URL: ${ALB_URL}/health"
RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" ${ALB_URL}/health)
echo "$RESPONSE"

echo ""
echo -e "${BLUE}6. ALB Target Health${NC}"
cd terraform
TARGET_GROUP_ARN=$(terraform output -json deployment_info | jq -r '.alb_target_group // empty')
if [ -z "$TARGET_GROUP_ARN" ]; then
    # Fallback: get target group from service
    TARGET_GROUP_ARN=$(aws elbv2 describe-target-groups \
        --region us-east-1 \
        --query "TargetGroups[?TargetGroupName=='equalify-pdf-tg'].TargetGroupArn" \
        --output text)
fi

if [ ! -z "$TARGET_GROUP_ARN" ]; then
    aws elbv2 describe-target-health \
        --target-group-arn ${TARGET_GROUP_ARN} \
        --region us-east-1 \
        --query 'TargetHealthDescriptions[*].{Target:Target.Id,Port:Target.Port,State:TargetHealth.State,Reason:TargetHealth.Reason,Description:TargetHealth.Description}' \
        --output json
else
    echo "Could not find target group ARN"
fi

cd ..

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Diagnostics Complete${NC}"
echo -e "${BLUE}========================================${NC}"
