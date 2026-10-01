@Library('internal-jenkins-lib') _

def ENV_PARAMETERS = env_parameters AWS_REGION, ENVIRONMENT

pipeline {
    agent any
    options {
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '100'))
    }
    parameters {
        string(name: 'REPO_BRANCH', defaultValue: 'master', description: 'Name of git branch to use', trim: true)
        string(name: 'JIRA_TICKET_NUMBER', defaultValue: 'ABC-1234', description: 'Provide the ticket number for reference. This is used to identify the security group rules in AWS.', trim: true)
        choice(name: 'AWS_REGION', choices: ['us-east-1','us-west-2'], description: 'AWS Region to deploy the stack')
        choice(name: 'ENVIRONMENT', choices: ['qa', 'uat', 'prod'], description: 'Environment identifier')
        string(name: 'OLD_FSX_STACK_NUMBER', defaultValue: '0', description: 'Enter the stack number of the OLD (source) FSX.', trim: true)
        string(name: 'NEW_FSX_STACK_NUMBER', defaultValue: '0', description: 'Enter the stack number of the NEW (target) FSX.', trim: true)
        choice(name: 'ACTION', choices: ['ENABLE', 'DISABLE'], description: 'Enable will add cross-stack connectivity security group rules between AWS stacks. Disable will remove the existing cross-stack connectivity security group rules between AWS stacks.')
    }

    stages {
        stage('FSX Cross-Stack Connectivity') {
            steps {
                script {
                    currentBuild.displayName = "#${BUILD_NUMBER}-${ENVIRONMENT}-${OLD_FSX_STACK_NUMBER}-to-${NEW_FSX_STACK_NUMBER}-${ACTION}"

                    withAWS(role: "${ENV_PARAMETERS['DeploymentRole']}", roleAccount: "${ENV_PARAMETERS['AwsAccount']}", duration: 10800, region: "${AWS_REGION}") {

                        sh """
                        python3 fsx_connectivity.py "${ENVIRONMENT}" "${OLD_FSX_STACK_NUMBER}" "${NEW_FSX_STACK_NUMBER}" "${AWS_REGION}" "${JIRA_TICKET_NUMBER}" "${ACTION}"
                        """
                    }
                }
            }
        }
    }
    post {
        always {
            cleanWs()
        }
    }
}