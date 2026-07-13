# CloudWatch Logs Insights Query: Error Breakdown
# Shows error types and rates

fields @timestamp, @message
| filter response.status_code >= 400
| stats count(*) as error_count
  by response.status_code as status, response.error.type as error_type
| sort error_count desc
