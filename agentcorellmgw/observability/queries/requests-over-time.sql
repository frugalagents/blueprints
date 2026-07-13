# CloudWatch Logs Insights Query: Request Volume Over Time
# Bin by 5-minute intervals

fields @timestamp, @message
| filter ispresent(response.status_code)
| stats count(*) as requests,
        sum(response.usage.input_tokens) as input_tokens,
        sum(response.usage.output_tokens) as output_tokens
  by bin(5m)
| sort @timestamp desc
