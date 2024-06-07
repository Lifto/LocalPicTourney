variable "access_key" {}
variable "secret_key" {}
variable "region" {
  default = "us-west-2"
}
variable "ocean" {
  type = "map"
  default = {}  # Must add 'name' and 'settings' to ocean.tfvars
}

// For SQS (and perhaps other areas) we guess the ARN of the resource based
// on the AWS keys. It so happens the names are derived from this key, not
// necessarily the one we use in our security vars.
variable "default_secret_key" {
  default = "1234567890"
}
